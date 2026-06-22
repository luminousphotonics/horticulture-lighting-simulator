from __future__ import annotations

import unittest

from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_IES_VARIANT,
    get_ies_comparator_profile,
    normalize_ies_variant,
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


if __name__ == "__main__":
    unittest.main()
