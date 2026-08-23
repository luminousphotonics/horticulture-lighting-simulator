from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import struct

import pytest

from fspm_optics.application import surface_flux_coefficient_analysis as a2
from fspm_optics.application import (
    surface_flux_optimized_calibration as resource,
)
from fspm_optics.application import (
    surface_flux_optimized_calibration_promotion as promotion,
)
from fspm_optics.application import surface_flux_display as historical_display
from fspm_optics.application.fspm_science import (
    COEFFICIENT_CLOSURE_ABS_TOLERANCE,
    LEAF_STRUCT,
    LOCAL_CLOSURE_ABS_TOLERANCE,
    LOCAL_CLOSURE_REL_TOLERANCE,
    PATCH_STRUCT,
    PLANT_STRUCT,
)
from fspm_optics.application.surface_flux_calibration import (
    CalibrationCriteria,
    _hash_json,
    _inventory,
    _pretty_json,
    _sha256_file,
)
from fspm_optics.application.surface_flux_display import (
    FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME,
    FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_EXPECTED_PLANT_COUNT,
    D5_PATCHES_PER_PLANT,
    D5_QUALITY_ORDER,
    SurfaceFluxRecalibrationConfig,
    _build_scientific_inputs,
    recalibration_jobs,
)
from fspm_optics.optics.rex_material_plan import (
    render_radiance_trans_material,
)
from fspm_optics.plants.radiance_scene_export import (
    DEFAULT_LEAF_MATERIAL_MODIFIER,
)
from fspm_optics.surface_flux_optimized_calibration_promotion_cli import (
    build_parser,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _material_authorities(tmp_path: Path) -> tuple[dict[str, object], ...]:
    config = SurfaceFluxRecalibrationConfig(output_directory=tmp_path)
    _scene, plan = _build_scientific_inputs(config)
    authorities = []
    for band in D5_BAND_ORDER:
        material = plan.material(band)
        coefficients = material.source_interval.coefficients
        authorities.append(
            {
                "band_id": band,
                "absorptance": coefficients.absorptance,
                "transmittance": coefficients.transmittance,
                "reflectance": coefficients.reflectance,
                "coefficient_sum": math.fsum(
                    (
                        coefficients.absorptance,
                        coefficients.transmittance,
                        coefficients.reflectance,
                    )
                ),
                "closure_abs_tolerance": COEFFICIENT_CLOSURE_ABS_TOLERANCE,
                "material_sha256": hashlib.sha256(
                    render_radiance_trans_material(
                        DEFAULT_LEAF_MATERIAL_MODIFIER,
                        material.parameters,
                    ).encode("utf-8")
                ).hexdigest(),
                "material_provenance_sha256": _hash_json(material.to_dict()),
            }
        )
    return tuple(authorities)


def _channels() -> tuple[a2._CoefficientChannel, ...]:
    channels = []
    for order_index, (family, channel_identity) in enumerate(
        (family, channel)
        for family in D5_QUALITY_ORDER
        for channel in a2.COEFFICIENT_CHANNEL_ORDER
    ):
        side, metric, channel = channel_identity
        values = []
        fits = []
        for local_patch_index in range(D5_PATCHES_PER_PLANT):
            seed = (order_index + 1) / 100.0 + local_patch_index / 1_000_000.0
            fit = a2._fit_local_patch(
                requested_levels=(250.0, 500.0),
                achieved_levels=(250.0, 500.0),
                means=(seed * 250.0, seed * 500.0),
                criteria=CalibrationCriteria(),
            )
            assert fit["promotion_eligible"] is True
            values.append(float(fit["coefficient_gamma"]))
            fits.append({"local_patch_index": local_patch_index, **fit})
        value_tuple = tuple(values)
        channels.append(
            a2._CoefficientChannel(
                family=family,
                side=side,
                metric=metric,
                channel=channel,
                values=value_tuple,
                patch_fits=tuple(fits),
                distribution=a2._distribution_summary(value_tuple),
            )
        )
    return tuple(channels)


def _derived_jobs(
    channels: tuple[a2._CoefficientChannel, ...],
    material_authorities: tuple[dict[str, object], ...],
) -> tuple[a2._DerivedJob, ...]:
    scene, _material_plan = _build_scientific_inputs(
        SurfaceFluxRecalibrationConfig(output_directory=Path("unused"))
    )
    counts = scene.counts.to_payload()
    coefficients = {
        (channel.family, channel.channel): channel.values for channel in channels
    }
    derived = []
    for job in recalibration_jobs():
        raw_hashes = {
            band: _digest(f"{job.job_id}:{band}") for band in D5_BAND_ORDER
        }
        values_by_channel = {}
        for _side, _metric, channel in a2.COEFFICIENT_CHANNEL_ORDER:
            gamma = coefficients[(job.quality, channel)]
            values_by_channel[channel] = tuple(
                gamma[local_patch_index] * job.requested_level
                for _plant_index in range(D5_EXPECTED_PLANT_COUNT)
                for local_patch_index in range(D5_PATCHES_PER_PLANT)
            )
        stage_c_materials = [
            {**record, "raw_receiver_sha256": raw_hashes[str(record["band_id"])]}
            for record in material_authorities
        ]
        closure = {
            "coefficient_abs_tolerance": COEFFICIENT_CLOSURE_ABS_TOLERANCE,
            "local_rel_tolerance": LOCAL_CLOSURE_REL_TOLERANCE,
            "local_abs_tolerance": LOCAL_CLOSURE_ABS_TOLERANCE,
            "maximum_band_density_closure_error_umol_m2_s": 0.0,
            "maximum_band_rate_closure_error_umol_s": 0.0,
            "maximum_par_or_far_red_density_closure_error_umol_m2_s": 0.0,
            "maximum_par_or_far_red_rate_closure_error_umol_s": 0.0,
            "maximum_patch_to_leaf_conservation_error_umol_s": 0.0,
            "maximum_leaf_to_plant_conservation_error_umol_s": 0.0,
            "maximum_plant_to_room_conservation_error_umol_s": 0.0,
        }
        derivation_digests = {
            name: {
                "sha256": _digest(f"{job.job_id}:stage-c:{name}"),
                "byte_length": rows * stride,
                "row_count": rows,
                "stride_bytes": stride,
            }
            for name, rows, stride in (
                ("patch", counts["patches"], PATCH_STRUCT.size),
                ("leaf", counts["leaves"], LEAF_STRUCT.size),
                ("plant", counts["plants"], PLANT_STRUCT.size),
            )
        }
        derived.append(
            a2._DerivedJob(
                job_id=job.job_id,
                family=job.quality,
                requested_level=job.requested_level,
                achieved_reference=job.requested_level,
                values_by_channel=values_by_channel,
                stage_c_validation={
                    "counts": counts,
                    "modeled_physical_one_sided_leaf_area_m2": 1.0,
                    "closure": closure,
                    "par_band_order": list(D5_BAND_ORDER),
                    "far_red_executed_or_aggregated": False,
                    "authoritative_phase27g_c_equations_reused": True,
                    "raw_receiver_sha256_by_band": raw_hashes,
                    "material_coefficient_authorities": stage_c_materials,
                    "derivation_digests": derivation_digests,
                },
                source_receiver_sha256_by_band=raw_hashes,
            )
        )
    return tuple(derived)


def _completed_a2(tmp_path: Path, name: str = "completed-a2") -> Path:
    output = tmp_path / name
    channels = _channels()
    jobs = _derived_jobs(channels, _material_authorities(tmp_path))
    source_completion_sha256 = _digest("synthetic-completed-d5-a1-v3")
    coefficient_payloads, coefficient_manifest = a2._coefficient_payloads(
        channels,
        source_completion_sha256=source_completion_sha256,
    )
    neutral_payloads, neutral_manifest = a2._neutral_payloads(
        jobs,
        channels,
        coefficient_payloads=coefficient_payloads,
        source_completion_sha256=source_completion_sha256,
    )
    config = SurfaceFluxRecalibrationConfig(output_directory=tmp_path / "unused")
    authenticated = a2._AuthenticatedSweep(
        input_root=tmp_path / "synthetic-d5-a1-authority",
        configuration=config,
        configuration_identity={},
        completion={
            "configuration_sha256": _digest("synthetic-d5-a1-config-v3"),
            "created_at_utc": "2026-07-15T12:00:00Z",
        },
        completion_sha256=source_completion_sha256,
        scene=None,
        results=(),
    )
    failures = {
        "failed_patch_fit_count": 0,
        "reason_counts": {
            "exact_zero_coefficient": 0,
            "through_origin_r_squared_below_minimum": 0,
            "relative_ratio_drift_above_maximum": 0,
        },
        "affected_local_patches": {
            "exact_zero_coefficient": [],
            "through_origin_r_squared_below_minimum": [],
            "relative_ratio_drift_above_maximum": [],
        },
    }
    report = a2._analysis_report(
        authenticated,
        jobs,
        channels,
        coefficient_manifest=coefficient_manifest,
        neutral_manifest=neutral_manifest,
        criteria=CalibrationCriteria(),
        failure_summary=failures,
        promotion_eligible=True,
    )
    a2._publish(
        output,
        authenticated=authenticated,
        coefficient_payloads=coefficient_payloads,
        neutral_payloads=neutral_payloads,
        coefficient_manifest=coefficient_manifest,
        neutral_manifest=neutral_manifest,
        report=report,
        promotion_eligible=True,
    )
    return output


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _rewrite_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(_pretty_json(value), encoding="utf-8")


def _reauth_report(a2_root: Path, report: dict[str, object]) -> None:
    report_path = a2_root / a2.D5_A2_ANALYSIS_NAME
    _rewrite_json(report_path, report)
    completion_path = a2_root / a2.D5_A2_COMPLETION_NAME
    completion = _json(completion_path)
    completion["report"] = {
        "path": a2.D5_A2_ANALYSIS_NAME,
        "byte_length": report_path.stat().st_size,
        "sha256": _sha256_file(report_path),
    }
    completion["ordered_artifact_inventory"] = _inventory(
        a2_root, excluded={a2.D5_A2_COMPLETION_NAME}
    )
    _rewrite_json(completion_path, completion)


def _reauth_coefficient_manifest(a2_root: Path) -> None:
    coefficient_path = a2_root / a2.D5_A2_COEFFICIENT_MANIFEST_NAME
    report_path = a2_root / a2.D5_A2_ANALYSIS_NAME
    report = _json(report_path)
    report["coefficient_manifest_sha256"] = _sha256_file(coefficient_path)
    _rewrite_json(report_path, report)
    completion_path = a2_root / a2.D5_A2_COMPLETION_NAME
    completion = _json(completion_path)
    completion["coefficient_manifest"] = {
        "path": a2.D5_A2_COEFFICIENT_MANIFEST_NAME,
        "byte_length": coefficient_path.stat().st_size,
        "sha256": _sha256_file(coefficient_path),
    }
    completion["report"] = {
        "path": a2.D5_A2_ANALYSIS_NAME,
        "byte_length": report_path.stat().st_size,
        "sha256": _sha256_file(report_path),
    }
    completion["ordered_artifact_inventory"] = _inventory(
        a2_root, excluded={a2.D5_A2_COMPLETION_NAME}
    )
    _rewrite_json(completion_path, completion)


def _reauth_candidate_payload(
    manifest: dict[str, object], payload: bytes
) -> dict[str, object]:
    digest = hashlib.sha256(payload).hexdigest()
    manifest["coefficient_payload"]["sha256"] = digest
    manifest["source_authority"]["d5_a2"]["combined_coefficient_payload"][
        "sha256"
    ] = digest
    for index, record in enumerate(manifest["arrays"]):
        start = index * resource.COEFFICIENT_ARRAY_BYTES
        record["slice_sha256"] = hashlib.sha256(
            payload[start : start + resource.COEFFICIENT_ARRAY_BYTES]
        ).hexdigest()
    manifest["byte_preservation"] = {
        "source_combined_payload_sha256": digest,
        "candidate_payload_sha256": digest,
        "individual_arrays_concatenated_byte_for_byte": True,
        "combined_payload_copied_without_transformation": True,
        "coefficient_values_reencoded": False,
        "coefficient_values_modified": False,
    }
    return manifest


def test_promotion_is_deterministic_and_copies_combined_bytes_unchanged(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    before = _inventory(source, excluded=set())
    first = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate-one",
    )
    second = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate-two",
    )

    combined = (
        source
        / a2.D5_A2_COEFFICIENT_DIRECTORY
        / a2.D5_A2_COMBINED_COEFFICIENT_NAME
    ).read_bytes()
    assert first.payload_path.read_bytes() == combined
    assert second.payload_path.read_bytes() == combined
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()
    assert _inventory(source, excluded=set()) == before
    assert {path.name for path in first.output_directory.iterdir()} == {
        resource.OPTIMIZED_CALIBRATION_MANIFEST_NAME,
        resource.OPTIMIZED_CALIBRATION_PAYLOAD_NAME,
    }
    assert len(combined) == 18_432


def test_loader_authenticates_order_offsets_slices_and_quality_mapping(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    published = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate",
    )
    loaded = resource.load_optimized_surface_flux_calibration(
        published.output_directory
    )

    assert loaded.select_display_quality("standard").coefficient_family == "standard"
    assert loaded.select_display_quality("quality").proxy is False
    assert loaded.select_display_quality("rigorous").coefficient_family == "rigorous"
    direct = loaded.select_display_quality("direct")
    assert direct.coefficient_family == "standard"
    assert direct.proxy_scope == "display-normalization-only"
    assert direct.raw_transport_proxied is False
    assert loaded.coefficient_for_display_quality(
        quality="direct", side="front", metric="incident", local_patch_index=17
    ) == loaded.coefficient_for_family(
        family="standard", side="front", metric="incident", local_patch_index=17
    )
    assert [record["order_index"] for record in loaded.manifest["arrays"]] == list(
        range(12)
    )
    assert [record["byte_offset"] for record in loaded.manifest["arrays"]] == [
        index * 1_536 for index in range(12)
    ]


@pytest.mark.parametrize(
    "mutation",
    (
        "truncated",
        "wrong_hash",
        "reordered",
        "substituted",
        "wrong_shape",
        "wrong_offset",
        "wrong_slice_hash",
    ),
)
def test_loader_rejects_truncated_hash_order_and_identity_substitution(
    tmp_path: Path,
    mutation: str,
) -> None:
    source = _completed_a2(tmp_path)
    published = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate",
    )
    manifest = json.loads(json.dumps(published.manifest))
    payload = published.payload_path.read_bytes()
    if mutation == "truncated":
        payload = payload[:-8]
    elif mutation == "wrong_hash":
        manifest["coefficient_payload"]["sha256"] = "0" * 64
    elif mutation == "reordered":
        manifest["arrays"][0], manifest["arrays"][1] = (
            manifest["arrays"][1],
            manifest["arrays"][0],
        )
    elif mutation == "substituted":
        manifest["arrays"][0]["family"] = "quality"
    elif mutation == "wrong_shape":
        manifest["coefficient_payload"]["shape"] = [3, 4, 191]
    elif mutation == "wrong_offset":
        manifest["arrays"][1]["byte_offset"] = 0
    else:
        manifest["arrays"][0]["slice_sha256"] = "0" * 64

    with pytest.raises(resource.OptimizedCalibrationResourceError):
        resource.validate_optimized_surface_flux_calibration(manifest, payload)


@pytest.mark.parametrize("invalid", (-1.0, float("nan"), float("inf")))
def test_loader_rejects_authenticated_negative_and_nonfinite_coefficients(
    tmp_path: Path,
    invalid: float,
) -> None:
    source = _completed_a2(tmp_path)
    published = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate",
    )
    manifest = json.loads(json.dumps(published.manifest))
    payload = bytearray(published.payload_path.read_bytes())
    payload[:8] = struct.pack("<d", invalid)
    rewritten = bytes(payload)
    _reauth_candidate_payload(manifest, rewritten)

    with pytest.raises(
        resource.OptimizedCalibrationResourceError,
        match="negative or non-finite",
    ):
        resource.validate_optimized_surface_flux_calibration(manifest, rewritten)


def test_loader_rejects_wrong_profile_unsupported_lookup_and_mapping(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    published = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate",
    )
    manifest = json.loads(json.dumps(published.manifest))
    manifest["scientific_identity"]["sampling_profile_id"] = "wrong-profile"
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        resource.validate_optimized_surface_flux_calibration(
            manifest, published.payload_path.read_bytes()
        )

    loaded = resource.load_optimized_surface_flux_calibration(
        published.output_directory
    )
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        loaded.select_display_quality("unknown")
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        loaded.coefficient_for_family(
            family="direct", side="front", metric="incident", local_patch_index=0
        )
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        loaded.coefficient_for_family(
            family="standard", side="back", metric="absorbed", local_patch_index=192
        )

    mapping = json.loads(json.dumps(published.manifest))
    mapping["quality_mapping"]["mappings"]["direct"][
        "raw_transport_proxied"
    ] = True
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        resource.validate_optimized_surface_flux_calibration(
            mapping, published.payload_path.read_bytes()
        )


def test_loader_preserves_exact_zero_without_threshold_or_substitution(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    published = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate",
    )
    manifest = json.loads(json.dumps(published.manifest))
    payload = bytearray(published.payload_path.read_bytes())
    payload[:8] = struct.pack("<d", 0.0)
    rewritten = bytes(payload)
    _reauth_candidate_payload(manifest, rewritten)

    loaded = resource.validate_optimized_surface_flux_calibration(
        manifest, rewritten
    )
    assert loaded.coefficient_for_family(
        family="standard", side="front", metric="incident", local_patch_index=0
    ) == 0.0


def test_loader_rejects_wrong_schema_version_and_extra_inventory(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    published = promotion.promote_optimized_surface_flux_calibration(
        input_directory=source,
        output_directory=tmp_path / "candidate",
    )
    manifest = json.loads(json.dumps(published.manifest))
    manifest["schema_version"] = 2
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        resource.validate_optimized_surface_flux_calibration(
            manifest, published.payload_path.read_bytes()
        )

    (published.output_directory / "unexpected.json").write_text(
        "{}\n", encoding="utf-8"
    )
    with pytest.raises(resource.OptimizedCalibrationResourceError):
        resource.load_optimized_surface_flux_calibration(
            published.output_directory
        )


def test_promotion_authenticates_all_a2_evidence_before_creating_output(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    neutral = next((source / a2.D5_A2_NEUTRAL_DIRECTORY).iterdir())
    data = bytearray(neutral.read_bytes())
    data[-1] ^= 1
    neutral.write_bytes(data)
    output = tmp_path / "must-remain-absent"

    with pytest.raises(promotion.OptimizedCalibrationPromotionError):
        promotion.promote_optimized_surface_flux_calibration(
            input_directory=source, output_directory=output
        )
    assert not output.exists()


def test_promotion_rejects_authenticated_combined_payload_substitution(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    combined_path = (
        source
        / a2.D5_A2_COEFFICIENT_DIRECTORY
        / a2.D5_A2_COMBINED_COEFFICIENT_NAME
    )
    combined = bytearray(combined_path.read_bytes())
    combined[:8] = struct.pack("<d", 0.75)
    combined_path.write_bytes(combined)
    coefficient_path = source / a2.D5_A2_COEFFICIENT_MANIFEST_NAME
    coefficient_manifest = _json(coefficient_path)
    combined_artifact = coefficient_manifest["combined_canonical_payload"][
        "artifact"
    ]
    combined_artifact["sha256"] = _sha256_file(combined_path)
    _rewrite_json(coefficient_path, coefficient_manifest)
    _reauth_coefficient_manifest(source)

    with pytest.raises(
        promotion.OptimizedCalibrationPromotionError,
        match="concatenate byte-for-byte",
    ):
        promotion.promote_optimized_surface_flux_calibration(
            input_directory=source,
            output_directory=tmp_path / "substituted-output",
        )


def test_promotion_rejects_wrong_profile_and_ineligible_completed_analysis(
    tmp_path: Path,
) -> None:
    wrong_profile = _completed_a2(tmp_path, "wrong-profile-a2")
    report = _json(wrong_profile / a2.D5_A2_ANALYSIS_NAME)
    report["fixed_identity"]["sampling_profile_id"] = "wrong-profile"
    _reauth_report(wrong_profile, report)
    with pytest.raises(promotion.OptimizedCalibrationPromotionError):
        promotion.promote_optimized_surface_flux_calibration(
            input_directory=wrong_profile,
            output_directory=tmp_path / "wrong-profile-output",
        )

    ineligible = _completed_a2(tmp_path, "ineligible-a2")
    completion_path = ineligible / a2.D5_A2_COMPLETION_NAME
    completion = _json(completion_path)
    completion["promotion_eligible"] = False
    _rewrite_json(completion_path, completion)
    with pytest.raises(promotion.OptimizedCalibrationPromotionError):
        promotion.promote_optimized_surface_flux_calibration(
            input_directory=ineligible,
            output_directory=tmp_path / "ineligible-output",
        )


def test_promotion_rejects_traversal_and_wrong_artifact_root(
    tmp_path: Path,
) -> None:
    source = _completed_a2(tmp_path)
    coefficient_path = source / a2.D5_A2_COEFFICIENT_MANIFEST_NAME
    coefficient_manifest = _json(coefficient_path)
    coefficient_manifest["coefficient_artifacts"][0]["artifact"]["path"] = (
        "../substituted.v1.f64le.bin"
    )
    _rewrite_json(coefficient_path, coefficient_manifest)
    _reauth_coefficient_manifest(source)

    with pytest.raises(promotion.OptimizedCalibrationPromotionError):
        promotion.promote_optimized_surface_flux_calibration(
            input_directory=source,
            output_directory=tmp_path / "traversal-output",
        )


def test_failed_atomic_promotion_leaves_no_candidate_or_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _completed_a2(tmp_path)
    output = tmp_path / "candidate"

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic rename failure")

    monkeypatch.setattr(promotion.os, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic rename failure"):
        promotion.promote_optimized_surface_flux_calibration(
            input_directory=source, output_directory=output
        )
    assert not output.exists()
    assert not tuple(tmp_path.glob(".candidate.*.tmp"))


def test_output_must_be_absent_and_isolated_from_input(tmp_path: Path) -> None:
    source = _completed_a2(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    for invalid in (source, source / "nested", existing, tmp_path):
        with pytest.raises(promotion.OptimizedCalibrationPromotionError):
            promotion.promote_optimized_surface_flux_calibration(
                input_directory=source, output_directory=invalid
            )


def test_cli_registration_packaging_and_historical_d2_resource_are_unchanged(
    tmp_path: Path,
) -> None:
    arguments = build_parser().parse_args(
        ["--input-dir", str(tmp_path / "a2"), "--output-dir", str(tmp_path / "b1")]
    )
    assert arguments.input_dir == tmp_path / "a2"
    assert arguments.output_dir == tmp_path / "b1"

    project_root = Path(__file__).parents[1]
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        "fspm-optics-promote-surface-flux-d5 = "
        '"fspm_optics.surface_flux_optimized_calibration_promotion_cli:main"'
    ) in pyproject
    assert '"resources/calibration/*.bin"' not in pyproject

    historical = (
        project_root
        / "src"
        / "fspm_optics"
        / "resources"
        / "calibration"
        / FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_NAME
    )
    assert FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256 == (
        "6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3"
    )
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == (
        FRONT_LOCAL_PATCH_CALIBRATION_RESOURCE_SHA256
    )
    reloaded = historical_display._load_front_local_patch_calibration()
    assert set(reloaded.profiles) == {"standard", "quality"}
    assert all(
        len(reloaded.profiles[family][metric].coefficients) == 192
        for family in ("standard", "quality")
        for metric in ("incident_par", "absorbed_par")
    )
