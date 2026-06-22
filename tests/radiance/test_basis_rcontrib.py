from __future__ import annotations

import unittest


from rad_rebuild.radiance.engine.simulation.basis_backends import (
    BASIS_BACKEND_RCONTRIB_MCPT,
    canonicalize_basis_backend,
    validate_basis_backend_request,
)
from rad_rebuild.radiance.engine.simulation.basis_rcontrib import (
    parse_rcontrib_ascii_output,
)


class BasisRcontribTests(unittest.TestCase):
    def test_parse_ascii_output_preserves_modifier_order_and_oversample(self) -> None:
        modifier_order = ["ring_0", "ring_1"]
        raw = "\n".join(
            [
                "3 3 3 9 9 9",
                "5 5 5 11 11 11",
                "7 7 7 13 13 13",
                "9 9 9 15 15 15",
            ]
        )
        matrix = parse_rcontrib_ascii_output(
            raw,
            sensor_count=2,
            oversample=2,
            modifier_order=modifier_order,
        )
        self.assertEqual(matrix.shape, (2, 2))
        self.assertAlmostEqual(matrix[0, 0], 4.0)
        self.assertAlmostEqual(matrix[0, 1], 10.0)
        self.assertAlmostEqual(matrix[1, 0], 8.0)
        self.assertAlmostEqual(matrix[1, 1], 14.0)

    def test_parse_ascii_output_rejects_malformed_row_width(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected 6 values"):
            parse_rcontrib_ascii_output(
                "1 2 3 4 5\n",
                sensor_count=1,
                oversample=1,
                modifier_order=["ring_0", "ring_1"],
            )

    def test_backend_validation_rejects_non_smd_rcontrib(self) -> None:
        backend = canonicalize_basis_backend("mcpt")
        self.assertEqual(backend, BASIS_BACKEND_RCONTRIB_MCPT)
        with self.assertRaisesRegex(
            ValueError, "only supported for Our LED System / SMD mode"
        ):
            validate_basis_backend_request("1000W HPS", backend)


if __name__ == "__main__":
    unittest.main()
