from __future__ import annotations

import json

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.metrics import _metrics_payload_for_request  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_LOCAL  # noqa: E402
from rad_rebuild.radiance.engine.plants.photoreceptor import (  # noqa: E402
    PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA,
    build_plant_photoreceptor_exposure_payload,
    write_plant_photoreceptor_exposure_artifact,
)


def _band(
    incident: float,
    absorbed: float,
    transmitted: float = 0.0,
) -> dict[str, float]:
    return {
        "incident_photon_flux_umol_s": incident,
        "absorbed_photon_flux_umol_s": absorbed,
        "reflected_photon_flux_umol_s": max(0.0, incident - absorbed - transmitted),
        "transmitted_photon_flux_umol_s": transmitted,
    }


def _spectral_payload() -> dict[str, object]:
    return {
        "schema": "rad_rebuild.fspm.plant_spectral_response.v1",
        "schema_version": 1,
        "status": "computed",
        "method": "surface_flux_band_weighted_leaf_absorptance_v1",
        "spectral_distribution": {
            "distribution_id": "test_distribution",
            "source": "test",
        },
        "plant_count": 2,
        "leaf_count": 2,
        "surface_count": 2,
        "leaf_summaries": [
            {
                "leaf_id": "plant_000_leaf_000",
                "plant_id": "plant_000",
                "leaf_index": 0,
                "surface_count": 1,
                "area_m2": 2.0,
                "absorbed_photon_flux_umol_s": 170.0,
                "band_totals": {
                    "blue": _band(80.0, 60.0),
                    "green": _band(70.0, 40.0),
                    "red": _band(90.0, 60.0),
                    "far_red": _band(40.0, 10.0, transmitted=8.0),
                },
            },
            {
                "leaf_id": "plant_001_leaf_000",
                "plant_id": "plant_001",
                "leaf_index": 0,
                "surface_count": 1,
                "area_m2": 1.0,
                "absorbed_photon_flux_umol_s": 105.0,
                "band_totals": {
                    "blue": _band(40.0, 20.0),
                    "green": _band(40.0, 25.0),
                    "red": _band(50.0, 35.0),
                    "far_red": _band(70.0, 25.0, transmitted=20.0),
                },
            },
        ],
    }


def test_photoreceptor_exposure_payload_reports_core_spectral_inputs() -> None:
    payload = build_plant_photoreceptor_exposure_payload(_spectral_payload())
    first_leaf = payload["leaf_summaries"][0]

    assert payload["schema"] == PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA
    assert payload["status"] == "computed"
    assert first_leaf["absorbed_blue_pfd_umol_m2_s"] == 30.0
    assert first_leaf["absorbed_green_pfd_umol_m2_s"] == 20.0
    assert first_leaf["absorbed_red_pfd_umol_m2_s"] == 30.0
    assert first_leaf["absorbed_far_red_pfd_umol_m2_s"] == 5.0
    assert first_leaf["absorbed_blue_fraction_of_par"] == 0.375
    assert first_leaf["absorbed_red_to_far_red_ratio_diagnostic"] == 6.0
    assert first_leaf["single_leaf_transmitted_far_red_pfd_umol_m2_s"] == 4.0
    assert first_leaf["single_leaf_far_red_transmittance_proxy"] == 0.2
    assert payload["exposure_consistency"]["plant_to_plant_absorbed_blue_pfd_cv"] > 0.0


def test_photoreceptor_exposure_pss_and_blue_dose_are_null_without_method_inputs() -> None:
    payload = build_plant_photoreceptor_exposure_payload(_spectral_payload())

    assert payload["phytochrome_pss_proxy"]["value"] is None
    assert payload["phytochrome_pss_proxy"]["status"] == "not_computed"
    assert "wavelength-dependent" in payload["phytochrome_pss_proxy"]["reason"]
    assert payload["blue_photon_dose"]["value_umol_m2"] is None
    assert payload["blue_photon_dose"]["reason"] == "recipe timing input not present"


def test_photoreceptor_exposure_artifact_export_is_deterministic(tmp_path) -> None:
    first_path = write_plant_photoreceptor_exposure_artifact(tmp_path, _spectral_payload())
    first = first_path.read_text(encoding="utf-8")
    second_path = write_plant_photoreceptor_exposure_artifact(tmp_path, _spectral_payload())
    second = second_path.read_text(encoding="utf-8")

    assert first_path == second_path
    assert first == second
    payload = json.loads(first)
    assert payload["source_artifact"] == "runtime_state/plant_spectral_response.json"


def test_photoreceptor_exposure_artifact_avoids_prohibited_language() -> None:
    payload = build_plant_photoreceptor_exposure_payload(_spectral_payload())
    text = json.dumps(payload, sort_keys=True).lower()

    for forbidden in (
        "canopy penetration",
        "compactness",
        "elongation",
        "expansion",
        "growth",
        "biomass",
        "harvest",
        "yield",
        "crop output",
        "crop_output",
    ):
        assert forbidden not in text


def test_metrics_payload_includes_photoreceptor_exposure_summary(tmp_path) -> None:
    runtime = tmp_path / "runtime_state"
    runtime.mkdir(parents=True)
    (tmp_path / "ppfd_map.txt").write_text(
        "0 0 0 100\n1 0 0 120\n0 1 0 110\n1 1 0 130\n",
        encoding="utf-8",
    )
    write_plant_photoreceptor_exposure_artifact(runtime, _spectral_payload())

    payload = _metrics_payload_for_request(
        RadianceRunRequest(
            action="metrics",
            execution_mode=EXECUTION_MODE_LIVE_LOCAL,
            plants_enabled=True,
        ),
        tmp_path,
    )

    exposure = payload["metrics"]["plant_photoreceptor_exposure"]
    assert exposure["schema"] == PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA
    assert exposure["source_artifact"] == "runtime_state/plant_photoreceptor_exposure.json"
    assert exposure["phytochrome_pss_proxy"]["value"] is None
