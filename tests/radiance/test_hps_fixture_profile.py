from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_IES_VARIANT,
    get_ies_comparator_profile,
    normalize_ies_variant,
)
from rad_rebuild.radiance.engine.emitters.hps_generation.rad_writer import (
    normalize_ies_light_rgb_to_grey_scalar_carrier,
)


class HpsFixtureProfileTests(unittest.TestCase):
    def test_karma_is_the_only_hps_ies_variant(self) -> None:
        self.assertEqual(DEFAULT_IES_VARIANT, "karma")
        self.assertEqual(normalize_ies_variant(None), "karma")
        self.assertEqual(normalize_ies_variant("KARMA"), "karma")
        self.assertEqual(get_ies_comparator_profile("karma").variant, "karma")

    def test_removed_hps_ies_variants_are_rejected(self) -> None:
        for variant in ("og", "hde", "legacy"):
            with (
                self.subTest(variant=variant),
                self.assertRaisesRegex(ValueError, "Unsupported HPS IES variant"),
            ):
                normalize_ies_variant(variant)

    def test_hps_ies_light_rgb_is_normalized_to_scalar_grey_carrier(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_hps_grey_") as tmpdir:
            rad_path = Path(tmpdir) / "hps.rad"
            rad_path.write_text(
                "\n".join(
                    [
                        "void brightdata hps_dist",
                        "5 flatcorr hps.dat source.cal src_phi src_theta",
                        "0",
                        "1 1",
                        "",
                        "hps_dist light hps_light",
                        "0",
                        "0",
                        "3 66.81492 24.42455 0.6250046",
                        "",
                        "hps_light polygon hps_aperture",
                        "0",
                        "0",
                        "12 0 0 0 1 0 0 1 1 0 0 1 0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            changed = normalize_ies_light_rgb_to_grey_scalar_carrier(rad_path)

            self.assertTrue(changed)
            text = rad_path.read_text(encoding="utf-8")
            grey = (66.81492 + 24.42455 + 0.6250046) / 3.0
            self.assertIn(f"3 {grey:.6f} {grey:.6f} {grey:.6f}", text)
            self.assertNotIn("3 66.81492 24.42455 0.6250046", text)


if __name__ == "__main__":
    unittest.main()
