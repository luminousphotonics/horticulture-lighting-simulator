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
    DEFAULT_FSPM_LEAF_OPTICAL_PROFILE_ID,
    FSPM_LEAF_OPTICAL_PROFILE_ID_ENV,
    PLANT_SPECTRAL_ABSORPTION_FILENAME,
    PLANT_SPECTRAL_ABSORPTION_SCHEMA,
    SOURCE_SPECTRAL_BASIS_BAND_FRACTION_LEGACY,
    build_banded_plant_spectral_absorption_payload,
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
        "fspm_spectral_transport_mode": "scalar_source_weighted",
        "leaf_radiance_material_mode": "rex_source_weighted_trans",
        "leaf_material_weighting_basis": "par_400_700_nm",
        "leaf_material_profile_id": "rex_green_butterhead_mature_leaf_optics_v1",
        "leaf_material_profile_version": "v0_2",
        "leaf_material_source_spectrum_id": "curve_data_smd",
        "leaf_material_source_spectrum_source": "curve_data_spd:/tmp/smd.csv",
        "leaf_material_effective_reflectance": 0.23,
        "leaf_material_effective_transmittance": 0.24,
        "leaf_material_effective_absorptance": 0.53,
        "leaf_material_radiance_primitive": "trans",
        "leaf_material_transmission_assumption": "diffuse_only",
        "leaf_material_specular_reflectance": 0.0,
        "leaf_material_specular_transmittance_fraction": 0.0,
        "leaf_material_radiance_red": 0.47,
        "leaf_material_radiance_green": 0.47,
        "leaf_material_radiance_blue": 0.47,
        "leaf_material_radiance_trans": 0.24 / 0.47,
        "leaf_material_radiance_tspec": 0.0,
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


def _banded_surface_flux_payload_for_cap(*, upper: float = 80.0) -> dict[str, object]:
    payload = _surface_flux_payload(density=100.0, area=2.0)
    payload.update(
        {
            "target_ppfd_umol_m2_s": 100.0,
            "target_tolerance_umol_m2_s": 20.0,
            "target_lower_threshold_umol_m2_s": 60.0,
            "target_upper_threshold_umol_m2_s": upper,
            "fspm_spectral_transport_mode": "banded_5",
            "leaf_radiance_material_mode": "rex_source_weighted_trans",
            "leaf_material_weighting_basis": "band_source_weighted",
            "leaf_material_profile_id": "fake_profile",
            "leaf_material_profile_version": "test",
            "leaf_material_source_spectrum_id": "fake_spd",
            "leaf_material_source_spectrum_source": "unit_test",
            "source_spectral_basis": "wavelength_resolved_spd",
            "source_spectrum_basis": "wavelength_resolved_spd",
            "source_spectrum_id": "fake_spd",
            "source_spectrum_source": "unit_test",
            "band_scaling_basis": "source_band_photon_fraction_relative_to_par",
            "receiver_trace_count": 3,
            "par_band_ids": ["blue", "red"],
            "epar_band_ids": ["blue", "red", "far_red"],
            "banded_transport_band_count": 3,
            "banded_transport_active_trace_count": 3,
            "banded_transport_bands": [
                {
                    "band_id": "blue",
                    "wavelength_min_nm": 400,
                    "wavelength_max_nm": 499,
                    "included_in_par": True,
                    "included_in_epar": True,
                    "source_photon_fraction_relative_to_par": 0.2,
                    "band_has_source_photons": True,
                    "receiver_trace_required": True,
                    "receiver_trace_executed": True,
                    "effective_reflectance": 0.2,
                    "effective_transmittance": 0.1,
                    "effective_absorptance": 0.7,
                },
                {
                    "band_id": "red",
                    "wavelength_min_nm": 625,
                    "wavelength_max_nm": 699,
                    "included_in_par": True,
                    "included_in_epar": True,
                    "source_photon_fraction_relative_to_par": 0.8,
                    "band_has_source_photons": True,
                    "receiver_trace_required": True,
                    "receiver_trace_executed": True,
                    "effective_reflectance": 0.3,
                    "effective_transmittance": 0.1,
                    "effective_absorptance": 0.6,
                },
                {
                    "band_id": "far_red",
                    "wavelength_min_nm": 700,
                    "wavelength_max_nm": 750,
                    "included_in_par": False,
                    "included_in_epar": True,
                    "source_photon_fraction_relative_to_par": 0.5,
                    "band_has_source_photons": True,
                    "receiver_trace_required": True,
                    "receiver_trace_executed": True,
                    "effective_reflectance": 0.4,
                    "effective_transmittance": 0.2,
                    "effective_absorptance": 0.4,
                },
            ],
        }
    )
    return payload


def _banded_rows_for_cap() -> dict[str, list[dict[str, object]]]:
    row_template = {
        "surface_id": "plant_000_leaf_000_face_0000",
        "plant_id": "plant_000",
        "leaf_id": "plant_000_leaf_000",
        "area_m2": 2.0,
    }
    return {
        "blue": [
            {
                **row_template,
                "incident_photon_flux_density_umol_m2_s": 20.0,
                "incident_photon_flux_umol_s": 40.0,
            }
        ],
        "red": [
            {
                **row_template,
                "incident_photon_flux_density_umol_m2_s": 80.0,
                "incident_photon_flux_umol_s": 160.0,
            }
        ],
        "far_red": [
            {
                **row_template,
                "incident_photon_flux_density_umol_m2_s": 50.0,
                "incident_photon_flux_umol_s": 100.0,
            }
        ],
    }


def test_rex_leaf_optical_profile_is_selected_by_default() -> None:
    profile = leaf_optical_profile_from_env({})

    assert profile is not None
    assert profile.profile_id == DEFAULT_FSPM_LEAF_OPTICAL_PROFILE_ID


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
    assert payload["fspm_spectral_transport_mode"] == "scalar_source_weighted"
    assert payload["leaf_radiance_material_mode"] == "rex_source_weighted_trans"
    assert payload["leaf_material_radiance_primitive"] == "trans"
    assert payload["leaf_material_radiance_trans"] == pytest.approx(0.24 / 0.47)
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


def test_banded_spectral_absorption_aggregates_par_and_epar() -> None:
    surface_flux_payload = {
        **_surface_flux_payload(density=300.0, area=2.0),
        "receiver_trace_count": 3,
        "fspm_spectral_transport_mode": "banded_5",
        "band_scaling_basis": "source_band_photon_fraction_relative_to_par",
        "banded_transport_band_count": 5,
        "banded_transport_active_trace_count": 3,
        "par_band_ids": ["blue", "green", "orange", "red"],
        "epar_band_ids": ["blue", "green", "orange", "red", "far_red"],
        "scalar_flux_basis": "par_ppfd_umol_m2_s",
        "leaf_radiance_material_mode": "rex_source_weighted_trans",
        "leaf_material_weighting_basis": "band_source_weighted",
        "leaf_material_profile_id": "fake_profile",
        "leaf_material_profile_version": "test",
        "leaf_material_source_spectrum_id": "fake_spd",
        "leaf_material_source_spectrum_source": "unit_test",
        "source_spectrum_id": "fake_spd",
        "source_spectrum_source": "unit_test",
        "source_spectral_basis": "wavelength_resolved_spd",
        "source_spectrum_basis": "wavelength_resolved_spd",
        "banded_transport_bands": [
            {
                "band_id": "blue",
                "wavelength_min_nm": 400,
                "wavelength_max_nm": 499,
                "included_in_par": True,
                "included_in_epar": True,
                "source_photon_fraction_relative_to_par": 0.2,
                "band_has_source_photons": True,
                "receiver_trace_required": True,
                "receiver_trace_executed": True,
                "effective_reflectance": 0.1,
                "effective_transmittance": 0.2,
                "effective_absorptance": 0.7,
            },
            {
                "band_id": "red",
                "wavelength_min_nm": 625,
                "wavelength_max_nm": 699,
                "included_in_par": True,
                "included_in_epar": True,
                "source_photon_fraction_relative_to_par": 0.8,
                "band_has_source_photons": True,
                "receiver_trace_required": True,
                "receiver_trace_executed": True,
                "effective_reflectance": 0.3,
                "effective_transmittance": 0.1,
                "effective_absorptance": 0.6,
            },
            {
                "band_id": "far_red",
                "wavelength_min_nm": 700,
                "wavelength_max_nm": 750,
                "included_in_par": False,
                "included_in_epar": True,
                "source_photon_fraction_relative_to_par": 0.5,
                "band_has_source_photons": True,
                "receiver_trace_required": True,
                "receiver_trace_executed": True,
                "effective_reflectance": 0.4,
                "effective_transmittance": 0.2,
                "effective_absorptance": 0.4,
            },
        ],
    }
    row_template = {
        "surface_id": "plant_000_leaf_000_face_0000",
        "plant_id": "plant_000",
        "leaf_id": "plant_000_leaf_000",
        "area_m2": 2.0,
    }

    payload = build_banded_plant_spectral_absorption_payload(
        surface_flux_payload,
        {
            "blue": [
                {
                    **row_template,
                    "incident_photon_flux_density_umol_m2_s": 20.0,
                    "incident_photon_flux_umol_s": 40.0,
                }
            ],
            "red": [
                {
                    **row_template,
                    "incident_photon_flux_density_umol_m2_s": 80.0,
                    "incident_photon_flux_umol_s": 160.0,
                }
            ],
            "far_red": [
                {
                    **row_template,
                    "incident_photon_flux_density_umol_m2_s": 50.0,
                    "incident_photon_flux_umol_s": 100.0,
                }
            ],
        },
        surface_flux_payload,
    )
    crop = payload["crop_summary"]

    assert payload["fspm_spectral_transport_mode"] == "banded_5"
    assert payload["leaf_radiance_material_mode"] == "rex_source_weighted_trans"
    assert payload["leaf_material_weighting_basis"] == "band_source_weighted"
    assert payload["leaf_material_profile_id"] == "fake_profile"
    assert payload["leaf_material_profile_version"] == "test"
    assert payload["leaf_material_source_spectrum_id"] == "fake_spd"
    assert payload["leaf_material_source_spectrum_source"] == "unit_test"
    assert payload["source_spectral_basis"] == "wavelength_resolved_spd"
    assert payload["source_spectrum_basis"] == "wavelength_resolved_spd"
    assert payload["optical_profile"] == {
        "profile_id": "fake_profile",
        "profile_version": "test",
    }
    assert payload["source_spectrum"]["distribution_id"] == "fake_spd"
    assert payload["source_spectrum"]["source_spectral_basis"] == (
        "wavelength_resolved_spd"
    )
    assert payload["receiver_trace_count"] == 3
    assert crop["area_m2"] == pytest.approx(2.0)
    assert crop["scalar_incident_par_ppfd_umol_m2_s"] == pytest.approx(100.0)
    assert crop["incident_par_ppfd_umol_m2_s"] == pytest.approx(100.0)
    assert crop["incident_epar_ppfd_umol_m2_s"] == pytest.approx(150.0)
    assert crop["absorbed_blue_ppfd_umol_m2_s"] == pytest.approx(14.0)
    assert crop["absorbed_red_ppfd_umol_m2_s"] == pytest.approx(48.0)
    assert crop["absorbed_far_red_ppfd_umol_m2_s"] == pytest.approx(20.0)
    assert crop["absorbed_par_ppfd_umol_m2_s"] == pytest.approx(62.0)
    assert crop["absorbed_epar_ppfd_umol_m2_s"] == pytest.approx(82.0)
    assert crop["reflected_par_ppfd_umol_m2_s"] == pytest.approx(26.0)
    assert crop["reflected_epar_ppfd_umol_m2_s"] == pytest.approx(46.0)
    assert crop["transmitted_par_ppfd_umol_m2_s"] == pytest.approx(12.0)
    assert crop["transmitted_epar_ppfd_umol_m2_s"] == pytest.approx(22.0)
    assert crop["fraction_basis"] == "par"
    assert crop["absorbed_fraction"] == pytest.approx(0.62)
    assert crop["reflected_fraction"] == pytest.approx(0.26)
    assert crop["transmitted_fraction"] == pytest.approx(0.12)
    assert crop["absorbed_fraction_of_incident_par"] == pytest.approx(0.62)
    assert crop["absorbed_fraction_of_incident_epar"] == pytest.approx(82.0 / 150.0)


def test_banded_target_capped_absorption_scales_response_metrics_only() -> None:
    surface_flux_payload = _banded_surface_flux_payload_for_cap(upper=80.0)
    payload = build_banded_plant_spectral_absorption_payload(
        surface_flux_payload,
        _banded_rows_for_cap(),
        surface_flux_payload,
    )
    crop = payload["crop_summary"]

    assert payload["raw_absorption_preserved"] is True
    assert payload["not_biological_prediction"] is True
    assert payload["target_saturation_cap_ppfd_umol_m2_s"] == pytest.approx(80.0)
    assert crop["absorbed_par_ppfd_umol_m2_s"] == pytest.approx(62.0)
    assert crop["absorbed_epar_ppfd_umol_m2_s"] == pytest.approx(82.0)
    assert crop["target_capped_absorbed_par_ppfd"] == pytest.approx(49.6)
    assert crop["target_capped_absorbed_epar_ppfd"] == pytest.approx(65.6)
    assert crop["target_capped_absorbed_blue_ppfd"] == pytest.approx(11.2)
    assert crop["target_capped_absorbed_red_ppfd"] == pytest.approx(38.4)
    assert crop["target_capped_absorbed_far_red_ppfd"] == pytest.approx(16.0)
    assert crop["excess_absorbed_par_ppfd_above_target_cap"] == pytest.approx(12.4)
    assert crop["excess_absorbed_epar_ppfd_above_target_cap"] == pytest.approx(16.4)
    assert crop["target_capped_absorbed_par_fraction_of_raw"] == pytest.approx(0.8)
    assert crop["target_capped_absorbed_epar_fraction_of_raw"] == pytest.approx(0.8)
    assert crop["target_effective_absorbed_fraction"] == pytest.approx(0.62)
    assert crop["over_target_absorbed_par_fraction_of_raw"] == pytest.approx(0.2)
    assert crop["under_target_leaf_fraction"] == pytest.approx(0.0)
    assert crop["in_target_leaf_fraction"] == pytest.approx(0.0)
    assert crop["over_target_leaf_fraction"] == pytest.approx(1.0)
    assert payload["surface_summaries"][0]["target_cap_scale"] == pytest.approx(0.8)


def test_banded_target_capped_absorption_is_raw_when_under_cap() -> None:
    surface_flux_payload = _banded_surface_flux_payload_for_cap(upper=120.0)
    payload = build_banded_plant_spectral_absorption_payload(
        surface_flux_payload,
        _banded_rows_for_cap(),
        surface_flux_payload,
    )
    crop = payload["crop_summary"]

    assert crop["absorbed_par_ppfd_umol_m2_s"] == pytest.approx(62.0)
    assert crop["target_capped_absorbed_par_ppfd"] == pytest.approx(62.0)
    assert crop["target_capped_absorbed_epar_ppfd"] == pytest.approx(82.0)
    assert crop["target_capped_absorbed_par_fraction_of_raw"] == pytest.approx(1.0)
    assert crop["excess_absorbed_par_ppfd_above_target_cap"] == pytest.approx(0.0)
    assert crop["under_target_leaf_fraction"] == pytest.approx(0.0)
    assert crop["in_target_leaf_fraction"] == pytest.approx(1.0)
    assert crop["over_target_leaf_fraction"] == pytest.approx(0.0)


def test_target_capped_absorption_handles_zero_incident_par() -> None:
    profile = _fake_profile()
    distribution = wavelength_photon_distribution_from_samples(
        [(400, 1), (500, 1), (600, 1), (700, 1), (738, 1)],
        profile.wavelength_nm,
        distribution_id="fake_spd",
        source="unit_test_spd",
    )
    surface_flux_payload = _surface_flux_payload(density=0.0, area=2.0)
    surface_flux_payload["target_upper_threshold_umol_m2_s"] = 80.0

    payload = build_plant_spectral_absorption_payload(
        surface_flux_payload,
        profile,
        distribution,
    )
    crop = payload["crop_summary"]

    assert crop["absorbed_par_ppfd_umol_m2_s"] == pytest.approx(0.0)
    assert crop["target_capped_absorbed_par_ppfd"] == pytest.approx(0.0)
    assert crop["target_capped_absorbed_par_fraction_of_raw"] == pytest.approx(0.0)
    assert crop["target_effective_absorbed_fraction"] == pytest.approx(0.0)
    assert payload["surface_summaries"][0]["target_cap_scale"] == pytest.approx(0.0)


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
