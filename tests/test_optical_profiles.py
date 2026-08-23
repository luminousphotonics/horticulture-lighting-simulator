from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from fspm_optics.optics import profiles as profiles_module
from fspm_optics.optics.profiles import (
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
    REX_LEAF_OPTICS_TREATMENT_ID,
    LeafOpticalTreatmentProfile,
    list_leaf_optical_profiles,
    load_leaf_optical_profile,
    load_rex_green_butterhead_mature_leaf_optics_v1,
)
from tests.helpers import optical_profile


def test_registered_profile_is_explicit_not_an_implicit_load() -> None:
    assert list_leaf_optical_profiles() == (REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,)
    with pytest.raises(KeyError, match="Unknown"):
        load_leaf_optical_profile("not_registered")


def test_default_rex_profile_loads_from_package_resources() -> None:
    profile = load_rex_green_butterhead_mature_leaf_optics_v1()
    assert profile.profile_id == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
    assert profile.species == "Lactuca sativa"
    assert profile.cultivar == "Rex"
    assert profile.treatment_id == REX_LEAF_OPTICS_TREATMENT_ID
    assert profile.profile_version == "0.2"
    assert profile.wavelength_nm[0] == 404
    assert profile.wavelength_nm[-1] == 797
    assert len(profile.wavelength_nm) == 394
    assert "Figure 7" in profile.source


def _packaged_resource_root() -> Path:
    return Path(profiles_module.__file__).resolve().parents[1] / "resources"


def test_injected_data_root_still_loads_rex_profile(tmp_path: Path) -> None:
    relative_dir = Path("data/radiance/plant_optics/lettuce_rex")
    source_dir = _packaged_resource_root() / relative_dir
    injected_dir = tmp_path / relative_dir
    injected_dir.mkdir(parents=True)
    for source in source_dir.iterdir():
        shutil.copy2(source, injected_dir / source.name)

    profile = load_leaf_optical_profile(
        REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
        data_root=tmp_path,
    )
    assert profile.profile_id == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
    assert profile.wavelength_nm[0] == 404
    assert profile.wavelength_nm[-1] == 797


def test_missing_injected_manifest_error_is_clear(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="manifest not found") as exc_info:
        load_rex_green_butterhead_mature_leaf_optics_v1(data_root=tmp_path)
    assert "rex_leaf_optical_profile_manifest_v0_2.json" in str(exc_info.value)


def test_missing_injected_csv_error_is_clear(tmp_path: Path) -> None:
    relative_dir = Path("data/radiance/plant_optics/lettuce_rex")
    source_dir = _packaged_resource_root() / relative_dir
    injected_dir = tmp_path / relative_dir
    injected_dir.mkdir(parents=True)
    shutil.copy2(
        source_dir / "rex_leaf_optical_profile_manifest_v0_2.json",
        injected_dir / "rex_leaf_optical_profile_manifest_v0_2.json",
    )
    with pytest.raises(FileNotFoundError, match="CSV not found") as exc_info:
        load_rex_green_butterhead_mature_leaf_optics_v1(data_root=tmp_path)
    assert "rex_leaf_optical_properties_digitized_v0_2.csv" in str(exc_info.value)


def test_profile_treatment_lookup_and_aligned_curves() -> None:
    profile = optical_profile()
    treatment = profile.treatment("mean")
    assert treatment.wavelength_nm == profile.wavelength_nm
    assert all(
        r + t + a == pytest.approx(1.0)
        for r, t, a in zip(
            profile.reflectance,
            profile.transmittance,
            profile.absorptance,
            strict=True,
        )
    )
    with pytest.raises(KeyError):
        profile.treatment("missing")


def test_profile_curves_must_be_sorted_and_aligned() -> None:
    with pytest.raises(ValueError):
        LeafOpticalTreatmentProfile(
            treatment_id="bad",
            treatment_label="Bad",
            wavelength_nm=(550, 450),
            reflectance=(0.1,),
            transmittance=(0.1, 0.1),
            absorptance=(0.8, 0.8),
            raw_reflectance=(0.1, 0.1),
            raw_transmittance=(0.1, 0.1),
            raw_absorptance=(0.8, 0.8),
            implied_absorptance=(0.8, 0.8),
            absorptance_basis=("fixture", "fixture"),
        )
