from __future__ import annotations

import pytest

from fspm_optics.optics.spectral import (
    LeafSpectralOpticalBand,
    SpectralPhotonDistribution,
    SpectralPhotonFraction,
    build_leaf_spectral_absorption_summary,
    default_leafy_green_spectral_bands,
)


def test_absorptance_is_energy_partition_remainder() -> None:
    band = LeafSpectralOpticalBand("blue", 400, 500, 0.2, 0.1)
    assert band.absorptance == pytest.approx(0.7)
    with pytest.raises(ValueError, match="may not exceed"):
        LeafSpectralOpticalBand("bad", 400, 500, 0.7, 0.4)


def test_band_weighted_absorption_math() -> None:
    bands = [
        LeafSpectralOpticalBand("blue", 400, 500, 0.2, 0.1),
        LeafSpectralOpticalBand("red", 600, 700, 0.1, 0.1),
    ]
    summary = build_leaf_spectral_absorption_summary(bands, {"blue": 0.25, "red": 0.75})
    assert summary["total_incident_photon_fraction"] == pytest.approx(1.0)
    assert summary["total_absorbed_photon_fraction"] == pytest.approx(0.775)
    assert summary["weighted_leaf_absorptance"] == pytest.approx(0.775)


def test_distribution_rejects_total_fraction_above_one() -> None:
    with pytest.raises(ValueError):
        SpectralPhotonDistribution(
            "invalid",
            (
                SpectralPhotonFraction("blue", 0.7),
                SpectralPhotonFraction("red", 0.6),
            ),
        )


def test_default_bands_are_valid_and_serializable() -> None:
    payloads = [band.to_payload() for band in default_leafy_green_spectral_bands()]
    assert payloads
    assert all(0.0 <= row["absorptance"] <= 1.0 for row in payloads)
