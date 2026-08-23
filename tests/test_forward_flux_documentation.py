from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_forward_flux_documentation_states_the_scientific_boundary() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    aperture = (
        ROOT / "docs" / "fixtures" / "proposed_aperture_ppe_calibration.md"
    ).read_text(encoding="utf-8")
    spectral = (
        ROOT
        / "docs"
        / "spectral"
        / "smd_source_and_rex_leaf_weighting_policy.md"
    ).read_text(encoding="utf-8")
    combined = "\n".join((readme, aperture, spectral))

    for required in (
        "completed-aperture forward",
        "2.6 µmol/J",
        "receiver plane",
        "rtrace -I+",
        "rfluxmtx",
        "non-authoritative",
        "physical stack",
        "Historical",
    ):
        assert required in combined
    normalized_aperture = " ".join(aperture.split())
    assert "source-dependent and nonconvergent" in readme
    assert (
        "scalar derivations from certified COB run trees"
        in normalized_aperture
    )
    assert "without auditable native run trees" in normalized_aperture
    assert (
        "newly traced or recertified Quality result"
        in normalized_aperture
    )


def test_current_documents_do_not_publish_retired_exact_values() -> None:
    documents = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "docs").rglob("*.md")
    )
    retired = (
        "0.810042877" + "5596422",
        "0.900773170" + "1881426",
        "3.209706636" + "558342",
        "2.886409238" + "2513387",
    )
    assert all(value not in documents for value in retired)
