from __future__ import annotations

from pathlib import Path

import pytest

from fspm_optics.transport.scalar_ppfd import (
    BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
    BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
    BASELINE_PPFD_PHOTOPIC_LUMINANCE_WEIGHTING_AVOIDED,
    BASELINE_PPFD_RGB_DECODE_METHOD,
    BASELINE_PPFD_TRANSPORT_BASIS,
    PPFD_CONVERSION_BASIS,
    PpfdMapSample,
    build_baseline_rtrace_argv,
    decode_grey_channel_ppfd,
    decode_rtrace_ppfd_map,
    format_ppfd_map,
    load_ppfd_map,
    parse_ppfd_map,
    parse_rtrace_rgb_rows,
    ppfd_max,
    ppfd_mean,
    scale_ppfd_map,
)


def test_scalar_ppfd_constants_describe_direct_grey_transport() -> None:
    assert BASELINE_PPFD_TRANSPORT_BASIS == "canopy_plane_scalar_par_ppfd"
    assert BASELINE_PPFD_RGB_DECODE_METHOD == "grey_channel_average_after_equality_assertion"
    assert "no_179" in PPFD_CONVERSION_BASIS
    assert BASELINE_PPFD_PHOTOPIC_LUMINANCE_WEIGHTING_AVOIDED is True


def test_rgb_channels_must_be_equal_for_scalar_ppfd() -> None:
    assert decode_grey_channel_ppfd(10.0, 10.0, 10.0) == pytest.approx(10.0)
    assert decode_grey_channel_ppfd(10.0, 10.0000001, 9.9999999) == pytest.approx(10.0)
    with pytest.raises(ValueError, match="must use grey scalar channels"):
        decode_grey_channel_ppfd(10.0, 9.0, 11.0, row_number=4)


def test_strict_scalar_rgb_decoder_tolerance_remains_unchanged() -> None:
    assert BASELINE_PPFD_RGB_EQUALITY_ABS_TOL == 1e-6
    assert BASELINE_PPFD_RGB_EQUALITY_REL_TOL == 1e-6
    with pytest.raises(ValueError, match="must use grey scalar channels"):
        decode_grey_channel_ppfd(0.9329, 0.9309, 0.8980)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("1 2\n", "expected at least three columns"),
        ("red green blue\n", "channels must be numeric"),
        ("\n", "No rtrace RGB rows"),
        ("1 -1 1\n", "must be non-negative"),
    ],
)
def test_malformed_rtrace_rows_fail_clearly(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_rtrace_rgb_rows(text)


def test_rtrace_ppfd_map_decode_averages_oversamples() -> None:
    coordinates = [
        (0.0, 0.0, 0.5),
        (0.0, 0.0, 0.5),
        (1.0, 0.0, 0.5),
        (1.0, 0.0, 0.5),
    ]
    samples = decode_rtrace_ppfd_map(
        coordinates,
        "10 10 10\n14 14 14\n20 20 20\n24 24 24\n",
        oversample=2,
    )
    assert samples == (
        PpfdMapSample(0.0, 0.0, 0.5, 12.0),
        PpfdMapSample(1.0, 0.0, 0.5, 22.0),
    )
    assert format_ppfd_map(samples).splitlines() == [
        "0.000000 0.000000 0.500000 12.000000",
        "1.000000 0.000000 0.500000 22.000000",
    ]


def test_ppfd_map_parse_load_mean_max_and_scale(tmp_path: Path) -> None:
    text = "# scalar map\n0,0,0.5,100\n1 0 0.5 300\n"
    parsed = parse_ppfd_map(text)
    assert ppfd_mean(parsed) == pytest.approx(200.0)
    assert ppfd_max(parsed) == pytest.approx(300.0)

    scaled = scale_ppfd_map(parsed, 0.25)
    assert [sample.ppfd_umol_m2_s for sample in scaled] == pytest.approx([25.0, 75.0])

    path = tmp_path / "ppfd_map.txt"
    path.write_text(format_ppfd_map(scaled), encoding="utf-8")
    assert load_ppfd_map(path) == scaled


def test_baseline_rtrace_argv_contains_transport_contract(tmp_path: Path) -> None:
    octree = tmp_path / "baseline.oct"
    options = ["-ab", "3", "-aa", "0.22"]
    argv = build_baseline_rtrace_argv(
        octree=octree,
        options=options,
        nthreads=5,
    )
    assert argv == [
        "rtrace",
        "-h",
        "-I+",
        "-n",
        "5",
        *options,
        str(octree),
    ]


def test_baseline_rtrace_argv_validates_thread_count() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        build_baseline_rtrace_argv(octree="scene.oct", options=(), nthreads=0)
