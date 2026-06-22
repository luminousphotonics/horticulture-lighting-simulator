from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import matplotlib.pyplot as plt
import numpy as np

from rad_rebuild.radiance.engine.photometry.ies_photon_toolkit import (
    FT2M,
    IesParseError,
    SpdParseError,
    load_relative_spd,
    parse_ies_numeric_header,
    read_ies_lines,
    run_ies2rad,
    summarize_ies_header,
)
from rad_rebuild.radiance.engine.photometry.ppfd_metrics import (
    compute_ppfd_metrics,
    metric_float,
)
from rad_rebuild.radiance.engine.validation.results import emit_validation_result
from rad_rebuild.radiance.engine.validation.validate_smd_curve_model import (
    run_curve_model_validation,
)
from rad_rebuild.radiance.engine.visualization.heatmaps import save_annotated_heatmap


class Phase10ScientificContractTests(unittest.TestCase):
    def tearDown(self) -> None:
        plt.close("all")

    def test_valid_ies_summary_preserves_header_units_and_counts(self) -> None:
        lines = [
            "IESNA:LM-63-2002",
            "[TEST] RAD_REBUILD_SYNTHETIC_PUBLIC_FIXTURE",
            "[MANUFAC] Public synthetic test fixture",
            "TILT=NONE",
            "2 1234 1 3 1 1 1 2 4 0.5 1 1 100",
            "0 90 180",
            "0",
            "10 20 10",
        ]
        with tempfile.TemporaryDirectory(prefix="rad_phase10_ies_") as tmpdir:
            ies_path = Path(tmpdir) / "synthetic_public.ies"
            ies_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            parsed_lines = read_ies_lines(ies_path)
            numeric = parse_ies_numeric_header(parsed_lines)
            summary = summarize_ies_header(ies_path, parsed_lines)

        self.assertEqual(parsed_lines[:3], lines[:3])
        self.assertEqual(numeric.units_type, 1)
        self.assertEqual(summary.lamp_count, 2)
        self.assertEqual(summary.lumens_per_lamp, 1234.0)
        self.assertEqual(summary.input_watts, 100.0)
        self.assertGreater(summary.total_luminaire_lumens, 0.0)
        self.assertAlmostEqual(summary.width_m, 2.0 * FT2M)
        self.assertAlmostEqual(summary.length_m, 4.0 * FT2M)
        self.assertAlmostEqual(summary.height_m, 0.5 * FT2M)

    def test_ies_parser_raises_typed_error_for_malformed_numeric_header(self) -> None:
        lines = [
            "IESNA:LM-63-2002",
            "TILT=NONE",
            "1 1000 1 2 1 1 1 1 1 1 1 1 100",
            "0 90",
            "0",
            "1 not-a-number",
        ]

        with self.assertRaisesRegex(IesParseError, "numeric token"):
            parse_ies_numeric_header(lines)

    def test_ies_parser_validates_candela_table_count(self) -> None:
        lines = [
            "IESNA:LM-63-2002",
            "TILT=NONE",
            "1 1000 1 2 2 1 1 1 1 1 1 1 100",
            "0 90",
            "0 180",
            "10 20 30",
        ]

        with self.assertRaisesRegex(IesParseError, "candela table"):
            summarize_ies_header(Path("malformed.ies"), lines)

    def test_spd_parser_raises_typed_error_for_missing_relative_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_phase10_spd_") as tmpdir:
            spd_path = Path(tmpdir) / "bad_spd.csv"
            spd_path.write_text("wavelength_nm,power\n400,1\n500,2\n", encoding="utf-8")

            with self.assertRaisesRegex(SpdParseError, "relative spectrum"):
                load_relative_spd(spd_path)

    def test_ppfd_metrics_reject_non_finite_and_negative_samples(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            compute_ppfd_metrics(np.array([10.0, np.nan, 20.0]))

        with self.assertRaisesRegex(ValueError, "non-negative"):
            compute_ppfd_metrics(np.array([10.0, -0.1, 20.0]))

    def test_ppfd_metrics_scaling_preserves_ratios_and_doubles_mean(self) -> None:
        field = np.array([800.0, 900.0, 1000.0, 1100.0], dtype=float)
        base = compute_ppfd_metrics(field, setpoint_ppfd=1000.0)
        doubled = compute_ppfd_metrics(field * 2.0, setpoint_ppfd=2000.0)

        self.assertAlmostEqual(float(doubled["mean"]), 2.0 * float(base["mean"]))
        self.assertAlmostEqual(
            float(doubled["peak_over_mean"]), float(base["peak_over_mean"])
        )
        self.assertAlmostEqual(
            float(doubled["min_over_max"]), float(base["min_over_max"])
        )
        self.assertEqual(
            sorted(
                ["min", "p05", "p50", "p95", "max"],
                key=lambda key: metric_float(base, key),
            ),
            ["min", "p05", "p50", "p95", "max"],
        )

    def test_annotated_heatmap_closes_figures_after_fallback_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_phase10_heatmap_") as tmpdir:
            save_annotated_heatmap(
                np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float),
                np.array([[0.0, 1.0]], dtype=float),
                np.array([[0.0], [1.0]], dtype=float),
                "x (m)",
                "y (m)",
                Path(tmpdir),
                0.0,
                10.0,
                "viridis",
                72,
                True,
            )

        self.assertEqual(plt.get_fignums(), [])

    def test_curve_model_validator_returns_machine_readable_payload(self) -> None:
        result = run_curve_model_validation()
        buffer = io.StringIO()
        emit_validation_result(result, stream=buffer)
        payload = json.loads(buffer.getvalue())

        self.assertEqual(payload["validator"], "smd_curve_model")
        self.assertTrue(payload["passed"])
        self.assertIn("metrics", payload)
        self.assertIn("checks", payload)
        self.assertGreater(payload["metrics"]["nominal_output_umol_s"], 0.0)

    def test_run_ies2rad_uses_resolved_executable_for_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_phase10_ies2rad_") as tmpdir:
            out_dir = Path(tmpdir) / "out"
            ies_path = Path(tmpdir) / "fixture.ies"
            ies_path.write_text("IESNA\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(
                cmd: list[str], *, cwd: Path, check: bool
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(cwd, out_dir)
                self.assertTrue(check)
                calls.append(cmd)
                if len(calls) == 1:
                    raise subprocess.CalledProcessError(1, cmd)
                (out_dir / "fixture.rad").write_text("# rad\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with (
                mock.patch(
                    "rad_rebuild.radiance.engine.photometry.ies_photon_toolkit.shutil.which",
                    return_value="/usr/bin/ies2rad",
                ),
                mock.patch(
                    "rad_rebuild.radiance.engine.photometry.ies_photon_toolkit.subprocess.run",
                    side_effect=fake_run,
                ),
                mock.patch(
                    "rad_rebuild.radiance.engine.photometry.ies_photon_toolkit._scale_ies_rad"
                ) as scale_mock,
                mock.patch(
                    "rad_rebuild.radiance.engine.photometry.ies_photon_toolkit.normalize_ies_rad_companion_paths"
                ) as normalize_mock,
            ):
                rad_path = run_ies2rad(out_dir, ies_path, "fixture", scale=1.25)

            self.assertEqual(rad_path, out_dir / "fixture.rad")
            self.assertEqual(calls[0][0], "/usr/bin/ies2rad")
            self.assertEqual(calls[1][0], "/usr/bin/ies2rad")
            self.assertIn("-m", calls[0])
            self.assertNotIn("-m", calls[1])
            scale_mock.assert_called_once_with(out_dir / "fixture.rad", 1.25)
            normalize_mock.assert_called_once_with(out_dir / "fixture.rad")


if __name__ == "__main__":
    unittest.main()
