from __future__ import annotations

from pathlib import Path

from fspm_optics.radiance.options import (
    radiance_option_value,
    radiance_options,
    replace_radiance_option_value,
)


def test_option_value_and_replacement_are_non_mutating() -> None:
    original = ["-ab", "3", "-aa", "0.22"]
    adjusted = replace_radiance_option_value(original, "-ab", "5")
    assert original == ["-ab", "3", "-aa", "0.22"]
    assert adjusted == ["-ab", "5", "-aa", "0.22"]
    assert radiance_option_value(adjusted, "-ab") == "5"
    assert radiance_option_value(adjusted, "-ad") is None


def test_missing_option_is_appended() -> None:
    assert replace_radiance_option_value(["-ab", "3"], "-af", "cache.amb") == [
        "-ab",
        "3",
        "-af",
        "cache.amb",
    ]


def test_option_presets_and_ambient_cache() -> None:
    direct = radiance_options("direct")
    standard = radiance_options("standard")
    assert radiance_option_value(direct, "-ab") == "0"
    assert radiance_option_value(standard, "-ab") == "3"
    assert radiance_options("instant") == standard
    assert radiance_options("unknown") == standard

    cache = Path("ambient/cache.amb")
    cached = radiance_options("quality", ambient_cache=cache)
    assert radiance_option_value(cached, "-ab") == "5"
    assert radiance_option_value(cached, "-af") == str(cache)


def test_new_transport_modules_do_not_import_a_process_runner() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    sources = [
        package_root / "transport" / "scalar_ppfd.py",
        package_root / "radiance" / "options.py",
    ]
    assert all("subprocess" not in path.read_text(encoding="utf-8") for path in sources)
