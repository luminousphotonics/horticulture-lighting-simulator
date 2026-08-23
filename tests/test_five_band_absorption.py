from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pytest

from fspm_optics import cli
from fspm_optics.plants.generator import generate_rex_butterhead_plant
from fspm_optics.receivers.samples import build_two_sided_patch_receivers
from fspm_optics.transport.basis.atomic import atomic_save_npy
from fspm_optics.transport.five_band import (
    FIVE_BAND_ORDER,
    plan_rex_five_band_transport,
    read_rex_five_band_transport_plan_json,
)
from fspm_optics.transport.five_band_absorption import (
    NPZ_ARRAY_ORDER,
    PAR_BAND_ORDER,
    FiveBandAbsorbedMetricsError,
    calculate_rex_five_band_absorbed_metrics,
    pair_two_sided_receivers,
    read_absorbed_patch_metrics_npz,
    read_absorbed_photon_summary_json,
    validate_incident_receiver_array,
)
from fspm_optics.transport.five_band_execution import (
    compute_five_band_incident_metrics,
    execute_rex_five_band_receiver_smoke,
)
from tests.support.five_band import (
    RecordingFiveBandRunner,
    fake_installation,
    solved_workspace,
)
from tests.support.workspace_templates import clone_five_band_workspace


def successful_phase19_workspace(tmp_path: Path):
    root = solved_workspace(tmp_path)
    plan = plan_rex_five_band_transport(root)
    execute_rex_five_band_receiver_smoke(
        plan,
        RecordingFiveBandRunner(),  # type: ignore[arg-type]
        radiance_installation=fake_installation(),
    )
    return root, plan


def synthetic_phase19_workspace(tmp_path: Path):
    root, plan = successful_phase19_workspace(tmp_path)
    samples = build_two_sided_patch_receivers(generate_rex_butterhead_plant())
    summary_path = plan.output_root / "five_band_receiver_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    arrays: dict[str, np.ndarray] = {}
    for band_index, band in enumerate(plan.band_plans):
        front_value = 10.0 + band_index
        back_value = 2.0 + band_index
        array = np.asarray(
            [
                front_value if sample.side == "front" else back_value
                for sample in samples
            ],
            dtype=np.float64,
        )
        arrays[band.band_id] = array
        atomic_save_npy(band.paths.decoded_pfd_path, array)
        digest = hashlib.sha256(band.paths.decoded_pfd_path.read_bytes()).hexdigest()
        record = summary["bands"][band_index]
        record["hashes"]["outputs"]["receiver_band_pfd_npy_sha256"] = digest
        record["metrics"] = compute_five_band_incident_metrics(
            band, array, samples
        ).to_dict()
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root, plan, samples, arrays


def update_phase19_npy_hash(plan, band_index: int) -> None:
    summary_path = plan.output_root / "five_band_receiver_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    band = plan.band_plans[band_index]
    summary["bands"][band_index]["hashes"]["outputs"][
        "receiver_band_pfd_npy_sha256"
    ] = hashlib.sha256(band.paths.decoded_pfd_path.read_bytes()).hexdigest()
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_phase19_plan(root: Path):
    return read_rex_five_band_transport_plan_json(
        root / "rex_five_band" / "five_band_transport_plan.json"
    )


@pytest.fixture(scope="module")
def successful_workspace_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root, _plan = successful_phase19_workspace(
        tmp_path_factory.mktemp("five-band-absorption-template")
    )
    return root


@pytest.fixture
def phase19_workspace_factory(
    successful_workspace_template: Path,
    tmp_path: Path,
) -> Callable[[str], Path]:
    def clone(name: str = "workspace") -> Path:
        return clone_five_band_workspace(
            successful_workspace_template,
            tmp_path / name,
        )

    return clone


def test_known_patch_leaf_and_plant_formulas_for_all_five_bands(
    tmp_path: Path,
) -> None:
    root, plan, _samples, _arrays = synthetic_phase19_workspace(tmp_path)

    result = calculate_rex_five_band_absorbed_metrics(root)

    first_pair = result.computed.patch_pairs[0]
    first_patch_rows = result.computed.patch_metrics[:5]
    total_area = result.computed.plant.physical_area_m2
    expected_par_flux = 0.0
    for band_index, (band, patch, aggregate) in enumerate(
        zip(plan.band_plans, first_patch_rows, result.computed.plant.bands, strict=True)
    ):
        front = 10.0 + band_index
        back = 2.0 + band_index
        incident_pfd = front + back
        absorptance = band.material.coefficients.absorptance
        expected_patch_flux = absorptance * incident_pfd * first_pair.area_m2
        expected_plant_flux = absorptance * incident_pfd * total_area

        assert patch.band_id == band.band_id
        assert patch.front_incident_pfd == front
        assert patch.back_incident_pfd == back
        assert patch.combined_incident_pfd == incident_pfd
        assert patch.front_absorbed_pfd == pytest.approx(absorptance * front)
        assert patch.back_absorbed_pfd == pytest.approx(absorptance * back)
        assert patch.combined_absorbed_flux == pytest.approx(expected_patch_flux)
        assert patch.front_absorbed_flux + patch.back_absorbed_flux == pytest.approx(
            patch.combined_absorbed_flux
        )
        assert aggregate.absorbed_flux == pytest.approx(expected_plant_flux)
        assert aggregate.area_weighted_absorbed_pfd == pytest.approx(
            absorptance * incident_pfd
        )
        assert aggregate.absorption_fraction == pytest.approx(absorptance)
        assert aggregate.absorbed_flux + aggregate.transmitted_flux + aggregate.reflected_flux == pytest.approx(
            aggregate.incident_flux
        )
        if band.band_id in PAR_BAND_ORDER:
            expected_par_flux += expected_plant_flux

    assert result.computed.plant.par.absorbed_flux == pytest.approx(expected_par_flux)
    assert result.computed.plant.par.absorbed_flux != pytest.approx(
        expected_par_flux + result.computed.plant.far_red.absorbed_flux
    )
    assert result.computed.plant.par.front_absorbed_flux + result.computed.plant.par.back_absorbed_flux == pytest.approx(
        result.computed.plant.par.absorbed_flux
    )
    assert len(result.computed.leaves) == 32
    assert all(leaf.patch_count == 16 for leaf in result.computed.leaves)
    assert sum(leaf.physical_area_m2 for leaf in result.computed.leaves) == pytest.approx(
        total_area
    )
    assert sum(leaf.par.absorbed_flux for leaf in result.computed.leaves) == pytest.approx(
        result.computed.plant.par.absorbed_flux
    )


def test_pairing_uses_explicit_side_metadata_not_row_parity() -> None:
    samples = build_two_sided_patch_receivers(generate_rex_butterhead_plant())
    reordered = tuple(
        item
        for index in range(0, len(samples), 2)
        for item in (samples[index + 1], samples[index])
    )

    pairs = pair_two_sided_receivers(reordered)

    assert pairs[0].front_receiver_index == 1
    assert pairs[0].back_receiver_index == 0
    assert pairs[0].leaf_id == samples[0].leaf_id
    assert pairs[0].patch_id == samples[0].patch_id


def test_pairing_rejects_duplicate_side_mismatched_identity_and_invalid_area() -> None:
    samples = list(build_two_sided_patch_receivers(generate_rex_butterhead_plant()))
    duplicate_side = list(samples)
    duplicate_side[1] = replace(duplicate_side[1], side="front")
    with pytest.raises(FiveBandAbsorbedMetricsError, match="duplicate front"):
        pair_two_sided_receivers(duplicate_side)

    duplicate_id = list(samples)
    duplicate_id[1] = replace(duplicate_id[1], receiver_id=samples[0].receiver_id)
    with pytest.raises(FiveBandAbsorbedMetricsError, match="duplicate receiver identity"):
        pair_two_sided_receivers(duplicate_id)

    mismatched_leaf = list(samples)
    mismatched_leaf[1] = replace(mismatched_leaf[1], leaf_id=samples[32].leaf_id)
    with pytest.raises(FiveBandAbsorbedMetricsError, match="multiple leaf"):
        pair_two_sided_receivers(mismatched_leaf)

    invalid_area = list(samples)
    invalid_area[0] = replace(invalid_area[0], area_m2=0.0)
    with pytest.raises(ValueError, match="front area must be positive"):
        pair_two_sided_receivers(invalid_area)


@pytest.mark.parametrize(
    ("array", "match"),
    [
        (np.ones(1023), "shape mismatch"),
        (np.concatenate(([-1.0], np.ones(1023))), "negative"),
        (np.concatenate(([np.nan], np.ones(1023))), "non-finite"),
        (np.asarray(["1"] * 1024), "real numeric"),
    ],
)
def test_receiver_array_validation_is_strict(array: np.ndarray, match: str) -> None:
    with pytest.raises(FiveBandAbsorbedMetricsError, match=match):
        validate_incident_receiver_array(array, band_id="blue")


def test_missing_npy_is_rejected(
    phase19_workspace_factory: Callable[[str], Path],
) -> None:
    root = phase19_workspace_factory("missing-npy")
    plan = read_phase19_plan(root)
    plan.band_plans[2].paths.decoded_pfd_path.unlink()

    with pytest.raises(FiveBandAbsorbedMetricsError, match="orange receiver NPY is missing"):
        calculate_rex_five_band_absorbed_metrics(root)


@pytest.mark.parametrize(
    ("replacement", "match"),
    [
        (np.ones(1023, dtype=np.float64), "shape mismatch"),
        (np.concatenate(([-1.0], np.ones(1023))), "negative"),
        (np.concatenate(([np.inf], np.ones(1023))), "non-finite"),
    ],
)
def test_malformed_phase19_array_values_are_rejected_after_hash_validation(
    phase19_workspace_factory: Callable[[str], Path],
    replacement: np.ndarray,
    match: str,
) -> None:
    root = phase19_workspace_factory("malformed-values")
    plan = read_phase19_plan(root)
    atomic_save_npy(plan.band_plans[0].paths.decoded_pfd_path, replacement)
    update_phase19_npy_hash(plan, 0)

    with pytest.raises(FiveBandAbsorbedMetricsError, match=match):
        calculate_rex_five_band_absorbed_metrics(root)


def test_malformed_npy_container_is_rejected(
    phase19_workspace_factory: Callable[[str], Path],
) -> None:
    root = phase19_workspace_factory("malformed-container")
    plan = read_phase19_plan(root)
    plan.band_plans[0].paths.decoded_pfd_path.write_bytes(b"not an npy")
    update_phase19_npy_hash(plan, 0)

    with pytest.raises(FiveBandAbsorbedMetricsError, match="blue receiver NPY is malformed"):
        calculate_rex_five_band_absorbed_metrics(root)


def test_npy_hash_summary_identity_and_material_identity_are_strict(
    phase19_workspace_factory: Callable[[str], Path],
) -> None:
    root = phase19_workspace_factory("npy-hash")
    plan = read_phase19_plan(root)
    blue = plan.band_plans[0].paths.decoded_pfd_path
    blue.write_bytes(blue.read_bytes() + b"tampered")
    with pytest.raises(FiveBandAbsorbedMetricsError, match="blue receiver NPY hash mismatch"):
        calculate_rex_five_band_absorbed_metrics(root)

    root2 = phase19_workspace_factory("identity")
    plan2 = read_phase19_plan(root2)
    summary_path = plan2.output_root / "five_band_receiver_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["transport_plan_identity"]["source_model_id"] = "wrong_source"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(FiveBandAbsorbedMetricsError, match="transport-plan identity mismatch"):
        calculate_rex_five_band_absorbed_metrics(root2)

    root3 = phase19_workspace_factory("material")
    plan3 = read_phase19_plan(root3)
    plan3.material_plan_path.write_text(
        plan3.material_plan_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(FiveBandAbsorbedMetricsError, match="shared input identity mismatch"):
        calculate_rex_five_band_absorbed_metrics(root3)


def test_json_and_npz_are_deterministic_typed_and_numeric(
    phase19_workspace_factory: Callable[[str], Path],
) -> None:
    root = phase19_workspace_factory("deterministic")
    first = calculate_rex_five_band_absorbed_metrics(root)
    first_json = first.summary_path.read_bytes()
    first_npz = first.patch_npz_path.read_bytes()
    second = calculate_rex_five_band_absorbed_metrics(root)

    assert second.summary_path.read_bytes() == first_json
    assert second.patch_npz_path.read_bytes() == first_npz
    assert read_absorbed_photon_summary_json(second.summary_path) == second.summary
    arrays = read_absorbed_patch_metrics_npz(second.patch_npz_path)
    assert tuple(arrays) == NPZ_ARRAY_ORDER
    assert arrays["patch_area_m2"].shape == (512,)
    assert arrays["front_incident_pfd"].shape == (512, 5)
    assert arrays["combined_absorbed_flux"].shape == (512, 5)
    assert all(not array.dtype.hasobject for array in arrays.values())
    assert second.summary.plant.far_red.quantity == "far_red_photon_flux"
    text = second.summary_path.read_text(encoding="utf-8")
    assert '"par_band_order": [\n    "blue",\n    "green",\n    "orange",\n    "red"' in text
    assert "far_red_policy" in text
    assert second.summary.to_payload()["units"] == {
        "photon_flux_density": "µmol m^-2 s^-1",
        "photon_flux": "µmol s^-1",
        "physical_area": "m^2",
        "absorptance_and_fractions": "dimensionless",
    }


def test_postprocessor_preserves_all_phase19_artifacts(
    phase19_workspace_factory: Callable[[str], Path],
) -> None:
    root = phase19_workspace_factory("read-only-postprocessor")
    plan = read_phase19_plan(root)
    source_paths = [
        plan.manifest_path,
        plan.material_plan_path,
        plan.source_model_path,
        plan.receiver_path,
        plan.output_root / "five_band_receiver_summary.json",
        *(band.paths.decoded_pfd_path for band in plan.band_plans),
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}

    calculate_rex_five_band_absorbed_metrics(root)

    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths
    }


class ExplodingRunner:
    def run(self, *_args, **_kwargs):
        raise AssertionError("Phase 20 must not invoke a runner")


def test_cli_writes_absorbed_artifacts_without_runner(
    phase19_workspace_factory: Callable[[str], Path],
    capsys,
) -> None:
    root = phase19_workspace_factory("cli")

    exit_code = cli.main(
        ["rex-five-band-absorbed-metrics", "--workspace", str(root)],
        runner=ExplodingRunner(),  # type: ignore[arg-type]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["command"] == "rex-five-band-absorbed-metrics"
    assert payload["success"] is True
    assert payload["par_band_order"] == list(PAR_BAND_ORDER)
    assert Path(payload["summary"]).is_file()
    assert Path(payload["patch_metrics"]).is_file()
