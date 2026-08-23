from __future__ import annotations

from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS


def test_fixed_transport_band_ids_and_ranges_match_policy() -> None:
    assert [
        (band.band_id, band.label, band.start_nm, band.end_nm)
        for band in FIXED_TRANSPORT_BANDS
    ] == [
        ("blue", "Blue", 400, 499),
        ("green", "Green", 500, 599),
        ("orange", "Orange", 600, 624),
        ("red", "Red", 625, 699),
        ("far_red", "Far-red", 700, 750),
    ]

    for left, right in zip(
        FIXED_TRANSPORT_BANDS[:-1],
        FIXED_TRANSPORT_BANDS[1:],
        strict=True,
    ):
        assert left.end_nm + 1 == right.start_nm
        assert left.contains(right.start_nm - 0.5)
        assert not left.contains(right.start_nm)
