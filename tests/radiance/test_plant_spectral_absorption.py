from __future__ import annotations

import json

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.optical_profiles import (  # noqa: E402
    LeafOpticalProfile,
    LeafOpticalTreatmentProfile,
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
    load_leaf_optical_profile,
)
from rad_rebuild.radiance.engine.plants.spectral import (  # noqa: E402
    SpectralPhotonDistribution,
    SpectralPhotonFraction,
)
from rad_rebuild.radiance.engine.plants.spectral_absorption import (  # noqa: E402
    FSPM_LEAF_OPTICAL_PROFILE_ID_ENV,
    PLANT_SPECTRAL_ABSORPTION_FILENAME,
    PLANT_SPECTRAL_ABSORPTION_SCHEMA,
    SOURCE_SPECTRAL_BASIS_BAND_FRACTION_LEGACY,
    build_plant_spectral_absorption_payload,
    leaf_optical_profile_from_env,
    wavelength_photon_distribution_from_band_fractions,
    wavelength_photon_distribution_from_samples,
    write_plant_spectral_absorption_artifact,
)


def _surface_flux_payload(*, density: float = 100.0, area: float = 2.0) -> dict[str, object]:
    return {
        "schema": "rad_rebuild.fspm.plant_surface_flux.v1",
        "schema_version": 1,
        "status": "computed",
        "method": "radiance_leaf_surface_receiver_sampling_v1",
        "baseline_transport_scene": "room_emitters_only",
        "fspm_receiver_transport_scene": "room_emitters_plants",
        "receiver_trace_count": 1,
        "receiver_sample_count": 1,
        "receiver_granularity": "leaf_centroid",
        "receiver_samples_per_leaf": 1.0,
        "receiver_generation_basis": "one_mesh_patch_centroid_nearest_leaf_area_centroid",
        "receiver_represented_area_m2": area,
        "receiver_sample_area_sum_m2": area,
        "receiver_area_basis": "one_sided_leaf_mesh_area_representative_sample_weights",
        "receiver_side_policy": "single_light_facing_side",
        "receiver_rows_per_mesh_surface_row": 1.0,
        "normal_generation_basis": "nearest_mesh_patch_to_leaf_area_centroid_oriented_upward",
        "receiver_granularity_role": "smoke_debug",
        "plant_count": 1,
        "leaf_count": 1,
        "surface_count": 1,
        "surface_summaries": [
            {
                "surface_id": "plant_000_leaf_000_face_0000",
                "plant_id": "plant_000",
                "leaf_id": "plant_000_leaf_000",
                "leaf_index": 0,
                "face_index": 0,
                "area_m2": area,
                "incident_photon_flux_density_umol_m2_s": density,
                "incident_photon_flux_umol_s": density * area,
            }
        ],
    }


def _fake_profile() -> LeafOpticalProfile:
    treatment = LeafOpticalTreatmentProfile(
        treatment_id="fake",
        treatment_label="Fake",
        wavelength_nm=(400, 500, 600, 700, 738),
        reflectance=(0.30, 0.20, 0.10, 0.20, 0.30),
        transmittance=(0.50, 0.50, 0.50, 0.30, 0.60),
        absorptance=(0.20, 0.30, 0.40, 0.50, 0.10),
        raw_reflectance=(0.30, 0.20, 0.10, 0.20, 0.30),
        raw_transmittance=(0.50, 0.50, 0.50, 0.30, 0.60),
        raw_absorptance=(0.20, 0.30, 0.40, 0.50, None),
        implied_absorptance=(0.20, 0.30, 0.40, 0.50, 0.10),
        absorptance_basis=(
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
            "implied_from_reflectance_transmittance",
        ),
    )
    return LeafOpticalProfile(
        profile_id="fake_profile",
        species="Lactuca sativa",
        cultivar="test",
        growth_stage="test",
        leaf_side_basis="test",
        wavelength_nm=treatment.wavelength_nm,
        reflectance=treatment.reflectance,
        transmittance=treatment.transmittance,
        absorptance=treatment.absorptance,
        source="unit_test",
        data_provenance="unit_test",
        validation_status="unit_test",
        raw_reflectance=treatment.raw_reflectance,
        raw_transmittance=treatment.raw_transmittance,
        raw_absorptance=treatment.raw_absorptance,
        implied_absorptance=treatment.implied_absorptance,
        absorptance_basis=treatment.absorptance_basis,
        treatment_id=treatment.treatment_id,
        treatment_label=treatment.treatment_label,
        profile_version="test",
        treatments=(treatment,),
    )


def test_no_leaf_optical_profile_is_selected_by_default() -> None:
    assert leaf_optical_profile_from_env({}) is None


def test_rex_leaf_optical_profile_is_loaded_only_when_selected() -> None:
    profile = leaf_optical_profile_from_env(
        {FSPM_LEAF_OPTICAL_PROFILE_ID_ENV: REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1}
    )

    assert profile is not None
    assert profile.profile_id == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1


def test_wavelength_spectral_absorption_uses_expected_flux_formula() -> None:
    profile = _fake_profile()
    distribution = wavelength_photon_distribution_from_samples(
        [(400, 1), (500, 1), (600, 1), (700, 1), (738, 1)],
        profile.wavelength_nm,
        distribution_id="fake_spd",
        source="unit_test_spd",
    )

    payload = build_plant_spectral_absorption_payload(
        _surface_flux_payload(density=100.0, area=2.0),
        profile,
        distribution,
    )
    surface = payload["surface_summaries"][0]

    assert distribution.wavelength_nm == tuple(sorted(distribution.wavelength_nm))
    assert distribution.band_fraction("par") == pytest.approx(1.0)
    assert surface["incident_par_ppfd_umol_m2_s"] == pytest.approx(100.0)
    assert payload["receiver_granularity"] == "leaf_centroid"
    assert payload["receiver_sample_count"] == 1
    assert payload["receiver_samples_per_leaf"] == pytest.approx(1.0)
    assert payload["receiver_generation_basis"] == (
        "one_mesh_patch_centroid_nearest_leaf_area_centroid"
    )
    assert payload["receiver_represented_area_m2"] == pytest.approx(2.0)
    assert payload["receiver_sample_area_sum_m2"] == pytest.approx(2.0)
    assert payload["receiver_side_policy"] == "single_light_facing_side"
    assert payload["normal_generation_basis"] == (
        "nearest_mesh_patch_to_leaf_area_centroid_oriented_upward"
    )
    assert payload["receiver_granularity_role"] == "smoke_debug"

    expected_absorbed_density = 100.0 * sum(
        fraction * absorptance
        for fraction, absorptance in zip(
            distribution.photon_fraction_per_nm,
            profile.absorptance,
            strict=True,
        )
    )
    expected_reflected_density = 100.0 * sum(
        fraction * reflectance
        for fraction, reflectance in zip(
            distribution.photon_fraction_per_nm,
            profile.reflectance,
            strict=True,
        )
    )
    expected_transmitted_density = 100.0 * sum(
        fraction * transmittance
        for fraction, transmittance in zip(
            distribution.photon_fraction_per_nm,
            profile.transmittance,
            strict=True,
        )
    )

    assert surface["total_absorbed_photon_flux_umol_s"] == pytest.approx(
        expected_absorbed_density * 2.0
    )
    assert surface["total_reflected_photon_flux_umol_s"] == pytest.approx(
        expected_reflected_density * 2.0
    )
    assert surface["total_transmitted_photon_flux_umol_s"] == pytest.approx(
        expected_transmitted_density * 2.0
    )
    assert (
        surface["total_absorbed_photon_flux_umol_s"]
        + surface["total_reflected_photon_flux_umol_s"]
        + surface["total_transmitted_photon_flux_umol_s"]
    ) == pytest.approx(surface["total_incident_photon_flux_umol_s"])


def test_spectral_absorption_payload_contains_profile_metadata_and_basis_audit() -> None:
    profile = _fake_profile()
    distribution = wavelength_photon_distribution_from_samples(
        [(400, 1), (500, 1), (600, 1), (700, 1), (738, 1)],
        profile.wavelength_nm,
        distribution_id="fake_spd",
        source="unit_test_spd",
    )

    payload = build_plant_spectral_absorption_payload(
        _surface_flux_payload(),
        profile,
        distribution,
    )

    assert payload["schema"] == PLANT_SPECTRAL_ABSORPTION_SCHEMA
    assert payload["optical_profile"]["profile_id"] == "fake_profile"
    assert payload["source_spectral_basis"] == "wavelength_resolved_spd"
    assert payload["scalar_flux_basis"] == "par_ppfd_umol_m2_s"
    assert payload["baseline_transport_scene"] == "room_emitters_only"
    assert payload["fspm_receiver_transport_scene"] == "room_emitters_plants"
    assert payload["receiver_trace_count"] == 1
    assert payload["units"]["optical_coefficients"] == "fraction"
    assert payload["spectral_grid"]["wavelength_nm"] == [400, 500, 600, 700, 738]
    assert payload["spectral_grid"]["absorptance_source_basis"][-1] == (
        "implied_from_reflectance_transmittance"
    )
    assert payload["absorptance_basis_coverage"]["raw_digitized_fraction"] > 0.0
    assert (
        payload["absorptance_basis_coverage"][
            "implied_from_reflectance_transmittance_fraction"
        ]
        > 0.0
    )
    assert "yield" in payload["outputs_do_not_predict"]


def test_legacy_band_fraction_fallback_is_explicitly_labeled() -> None:
    profile = _fake_profile()
    distribution = wavelength_photon_distribution_from_band_fractions(
        SpectralPhotonDistribution(
            "legacy",
            (
                SpectralPhotonFraction("blue", 0.25),
                SpectralPhotonFraction("green", 0.25),
                SpectralPhotonFraction("red", 0.40),
                SpectralPhotonFraction("far_red", 0.10),
            ),
        ),
        profile.wavelength_nm,
    )

    assert distribution.source_spectral_basis == SOURCE_SPECTRAL_BASIS_BAND_FRACTION_LEGACY
    assert distribution.band_fraction("par") == pytest.approx(1.0)
    assert distribution.band_fraction("far_red") > 0.0


def test_rex_profile_budget_is_reasonable_on_wavelength_grid() -> None:
    profile = load_leaf_optical_profile(REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1)
    distribution = wavelength_photon_distribution_from_samples(
        [(wavelength, 1.0) for wavelength in profile.wavelength_nm],
        profile.wavelength_nm,
        distribution_id="flat_test_spd",
        source="unit_test_spd",
    )
    payload = build_plant_spectral_absorption_payload(
        _surface_flux_payload(density=100.0, area=1.0),
        profile,
        distribution,
    )

    for absorptance, reflectance, transmittance in zip(
        payload["spectral_grid"]["absorptance"],
        payload["spectral_grid"]["reflectance"],
        payload["spectral_grid"]["transmittance"],
        strict=True,
    ):
        assert absorptance + reflectance + transmittance == pytest.approx(1.0)
    assert payload["crop_summary"]["incident_par_ppfd_umol_m2_s"] == pytest.approx(100.0)
    assert payload["crop_summary"]["absorbed_blue_ppfd_umol_m2_s"] > 0.0
    assert payload["crop_summary"]["absorbed_green_ppfd_umol_m2_s"] > 0.0
    assert payload["crop_summary"]["absorbed_orange_ppfd_umol_m2_s"] > 0.0
    assert payload["crop_summary"]["absorbed_red_ppfd_umol_m2_s"] > 0.0
    assert payload["crop_summary"]["absorbed_far_red_ppfd_umol_m2_s"] > 0.0


def test_spectral_absorption_artifact_write_is_deterministic(tmp_path) -> None:
    profile = _fake_profile()
    distribution = wavelength_photon_distribution_from_samples(
        [(400, 1), (500, 1), (600, 1), (700, 1), (738, 1)],
        profile.wavelength_nm,
        distribution_id="fake_spd",
        source="unit_test_spd",
    )

    first = write_plant_spectral_absorption_artifact(
        tmp_path,
        _surface_flux_payload(),
        profile,
        distribution,
    )
    second = write_plant_spectral_absorption_artifact(
        tmp_path,
        _surface_flux_payload(),
        profile,
        distribution,
    )

    assert first == second
    assert first.name == PLANT_SPECTRAL_ABSORPTION_FILENAME
    assert json.loads(first.read_text(encoding="utf-8")) == json.loads(
        second.read_text(encoding="utf-8")
    )
