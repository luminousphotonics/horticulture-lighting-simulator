from __future__ import annotations

import pytest

from fspm_optics.optics.spectral_absorption import (
    build_plant_spectral_absorption_payload,
    wavelength_photon_distribution_from_samples,
)
from fspm_optics.plants import PlantGeometryConfig, generate_plant_scene
from fspm_optics.receivers.aggregation import (
    BASELINE_PPFD_PROXY_METHOD,
    build_baseline_proxy_surface_flux_rows,
    build_plant_surface_flux_payload,
)
from tests.helpers import optical_profile, photon_distribution


def test_wavelength_distribution_normalizes_par_photons() -> None:
    distribution = wavelength_photon_distribution_from_samples(
        [(400.0, 1.0), (750.0, 1.0)],
        (450, 550, 650, 725),
        distribution_id="flat",
        source="unit fixture",
    )
    assert distribution.band_fraction("par") == pytest.approx(1.0)
    assert distribution.band_fraction("far_red") > 0.0


def test_surface_spectral_absorption_preserves_flux_partition() -> None:
    scene = generate_plant_scene(
        PlantGeometryConfig(plant_grid_rows=1, plant_grid_columns=1, leaf_count_per_plant=1)
    )
    rows = build_baseline_proxy_surface_flux_rows(scene, 200.0)
    surface_payload = build_plant_surface_flux_payload(
        scene,
        rows,
        method=BASELINE_PPFD_PROXY_METHOD,
    )
    payload = build_plant_spectral_absorption_payload(
        surface_payload,
        optical_profile(),
        photon_distribution(),
    )
    first = payload["surface_summaries"][0]
    assert first["total_incident_photon_flux_umol_s"] == pytest.approx(
        first["scalar_incident_par_photon_flux_umol_s"] * 1.1
    )
    assert (
        first["total_absorbed_photon_flux_umol_s"]
        + first["total_reflected_photon_flux_umol_s"]
        + first["total_transmitted_photon_flux_umol_s"]
    ) == pytest.approx(first["total_incident_photon_flux_umol_s"])


def test_profile_and_distribution_grids_must_align() -> None:
    distribution = photon_distribution()
    bad = type(distribution)(
        distribution_id="bad",
        wavelength_nm=(450, 550),
        photon_fraction_per_nm=(0.5, 0.5),
        source_spectral_basis="unit_fixture",
        source="unit fixture",
    )
    with pytest.raises(ValueError, match="align"):
        build_plant_spectral_absorption_payload(
            {"schema": "fspm_optics.fspm.plant_surface_flux.v1", "surface_summaries": [{}]},
            optical_profile(),
            bad,
        )
