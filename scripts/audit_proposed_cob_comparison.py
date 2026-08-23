#!/usr/bin/env python3
"""Execute the diagnostic-only Proposed native-SMD versus COB audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fspm_optics.diagnostics.proposed_cob_audit import (  # noqa: E402
    AUDIT_SCHEMA_ID,
    AUDIT_SCHEMA_VERSION,
    ProposedCobAuditError,
    artifact_with_identity,
    authenticate_certified_archive,
    current_source_identities,
    evaluate_receiver_acceptance,
    execute_direct_cases,
    execute_fixture_interception,
    execute_matched_characterization,
    hash_json,
    inventory_tree,
    sha256_file,
)
from fspm_optics.fixtures.proposed_cob.source import (  # noqa: E402
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
)
from fspm_optics.transport.basis.atomic import atomic_write_text  # noqa: E402

DEFAULT_ARCHIVE = Path(
    "/home/austin/Documents/fspm-cob-quality-certified-20260729"
)

CERTIFIED_QUALITY = {
    "10x10_aisle_off": {
        "run_id": "c25ac75d8fa24a5ca2c920cf2201dbf9",
        "ppf_umol_s": 6212.81,
        "power_w": 2389.54,
        "dimming_factor": 0.39173,
        "cv_percent": 1.900,
    },
    "30x50_aisle_on": {
        "run_id": "88c49f07607c480d8ef8c7a04113166e",
        "ppf_umol_s": 67600.82,
        "power_w": 26000.31,
        "dimming_factor": 0.40945,
        "cv_percent": 6.245,
    },
}
HISTORICAL_NATIVE_QUALITY = {
    "10x10_aisle_off": {
        "ppf_umol_s": 5624.81,
        "power_w": 2163.39,
        "dimming_factor": 0.35465,
        "cv_percent": 1.984,
    },
    "30x50_aisle_on": {
        "ppf_umol_s": 61026.72,
        "power_w": 23471.81,
        "dimming_factor": 0.36963,
        "cv_percent": 6.660,
    },
}


def _run_text(*args: str) -> str:
    result = subprocess.run(
        args,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return (result.stdout + result.stderr).strip()


def _write_json(path: Path, payload: object) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _source_metric(
    characterization: dict[str, object],
    source_mode: str,
    name: str,
) -> float:
    modes = characterization["source_modes"]
    assert isinstance(modes, dict)
    source = modes[source_mode]
    assert isinstance(source, dict)
    value = source[name]
    assert isinstance(value, int | float) and not isinstance(value, bool)
    return float(value)


def _comparison_payload(
    *,
    characterization: dict[str, object],
    acceptance: dict[str, object],
    interception: dict[str, object],
    direct: dict[str, object],
) -> dict[str, object]:
    native_aperture_ppf = _source_metric(
        characterization, NATIVE_SOURCE_MODE, "completed_aperture_ppf_umol_s"
    )
    cob_aperture_ppf = _source_metric(
        characterization, COB_SOURCE_MODE, "completed_aperture_ppf_umol_s"
    )
    characterization_modes = characterization["source_modes"]
    assert isinstance(characterization_modes, dict)
    native_angular_ppf = float(
        characterization_modes[NATIVE_SOURCE_MODE]["metrics"][
            "integrated_angular_ppf_umol_s"
        ]
    )
    cob_angular_ppf = float(
        characterization_modes[COB_SOURCE_MODE]["metrics"][
            "integrated_angular_ppf_umol_s"
        ]
    )
    native_trace_yield = native_angular_ppf / native_aperture_ppf
    cob_trace_yield = cob_angular_ppf / cob_aperture_ppf
    trace_yield_required_ratio = native_trace_yield / cob_trace_yield
    rooms: dict[str, object] = {}
    acceptance_rooms = acceptance["rooms"]
    interception_rooms = interception["rooms"]
    direct_pairs = direct["paired_invariants"]
    assert isinstance(acceptance_rooms, dict)
    assert isinstance(interception_rooms, dict)
    assert isinstance(direct_pairs, dict)
    for room_id in ("10x10_aisle_off", "30x50_aisle_on"):
        acceptance_room = acceptance_rooms[room_id]
        interception_room = interception_rooms[room_id]
        direct_room = direct_pairs[room_id]
        assert isinstance(acceptance_room, dict)
        assert isinstance(interception_room, dict)
        assert isinstance(direct_room, dict)
        acceptance_modes = acceptance_room["source_modes"]
        interception_modes = interception_room["source_modes"]
        assert isinstance(acceptance_modes, dict)
        assert isinstance(interception_modes, dict)
        native_accept = float(
            acceptance_modes[NATIVE_SOURCE_MODE][
                "geometric_receiver_domain_capture_fraction"
            ]
        )
        cob_accept = float(
            acceptance_modes[COB_SOURCE_MODE][
                "geometric_receiver_domain_capture_fraction"
            ]
        )
        native_intercept = float(
            interception_modes[NATIVE_SOURCE_MODE][
                "fixture_body_interception_fraction"
            ]
        )
        cob_intercept = float(
            interception_modes[COB_SOURCE_MODE][
                "fixture_body_interception_fraction"
            ]
        )
        metrics = direct_room["metrics"]
        assert isinstance(metrics, dict)
        direct_ppf_ratio = float(
            metrics["emitted_completed_aperture_ppf_umol_s"][
                "cob_to_native_ratio"
            ]
        )
        direct_efficiency_native = float(
            metrics["receiver_to_emitted_ppf_ratio"][NATIVE_SOURCE_MODE]
        )
        direct_efficiency_cob = float(
            metrics["receiver_to_emitted_ppf_ratio"][COB_SOURCE_MODE]
        )
        quality_cob = CERTIFIED_QUALITY[room_id]
        quality_native = HISTORICAL_NATIVE_QUALITY[room_id]
        quality_historical_ratio = (
            quality_cob["ppf_umol_s"] / quality_native["ppf_umol_s"]
        )
        geometric_ratio = native_accept / cob_accept
        geometric_body_ratio = (
            native_accept * (1.0 - native_intercept)
        ) / (cob_accept * (1.0 - cob_intercept))
        angular_trace_geometric_body_ratio = (
            trace_yield_required_ratio * geometric_body_ratio
        )
        rooms[room_id] = {
            "geometric_acceptance_required_ppf_ratio_cob_to_native": (
                geometric_ratio
            ),
            "geometric_plus_independent_body_factor_ratio_cob_to_native": (
                geometric_body_ratio
            ),
            "angular_trace_yield_plus_geometric_plus_body_ratio_cob_to_native": (
                angular_trace_geometric_body_ratio
            ),
            "direct_required_ppf_ratio_cob_to_native": direct_ppf_ratio,
            "direct_transport_efficiency_ratio_native_over_cob": (
                direct_efficiency_native / direct_efficiency_cob
            ),
            "historical_quality_ppf_ratio_cob_to_native": (
                quality_historical_ratio
            ),
            "certified_cob_quality": quality_cob
            | {
                "authentication": "certified raw run tree revalidated",
                "authenticated": True,
            },
            "recorded_native_quality": quality_native
            | {
                "authentication": (
                    "historical comparison only; no auditable raw artifacts"
                ),
                "authenticated": False,
            },
            "agreement": {
                "direct_minus_geometric_ratio": (
                    direct_ppf_ratio - geometric_ratio
                ),
                "direct_minus_geometric_plus_body_ratio": (
                    direct_ppf_ratio - geometric_body_ratio
                ),
                "direct_minus_angular_trace_geometric_body_ratio": (
                    direct_ppf_ratio - angular_trace_geometric_body_ratio
                ),
                "historical_quality_minus_direct_ratio": (
                    quality_historical_ratio - direct_ppf_ratio
                ),
            },
        }
    native_transmission = _source_metric(
        characterization, NATIVE_SOURCE_MODE, "stack_transmission"
    )
    cob_transmission = _source_metric(
        characterization, COB_SOURCE_MODE, "stack_transmission"
    )
    return {
        "source_normalization": {
            "native_stack_transmission": native_transmission,
            "cob_stack_transmission": cob_transmission,
            "native_required_internal_ppe_umol_per_j": _source_metric(
                characterization,
                NATIVE_SOURCE_MODE,
                "required_internal_ppe_umol_per_j",
            ),
            "cob_required_internal_ppe_umol_per_j": _source_metric(
                characterization,
                COB_SOURCE_MODE,
                "required_internal_ppe_umol_per_j",
            ),
            "completed_aperture_ppe_both_umol_per_j": 2.6,
            "far_field_rtrace_integrated_ppf_umol_s": {
                NATIVE_SOURCE_MODE: native_angular_ppf,
                COB_SOURCE_MODE: cob_angular_ppf,
            },
            "far_field_rtrace_to_rfluxmtx_completed_ppf_ratio": {
                NATIVE_SOURCE_MODE: native_trace_yield,
                COB_SOURCE_MODE: cob_trace_yield,
            },
            "required_ppf_ratio_from_source_mode_trace_response": (
                trace_yield_required_ratio
            ),
            "interpretation": (
                "different stack transmission changes required internal PPE, "
                "not completed-aperture PPF normalization; the separate "
                "source-mode-dependent rtrace/rfluxmtx response mismatch is a "
                "solver/model diagnostic, not a physical transmission claim"
            ),
        },
        "rooms": rooms,
        "supported_conclusions": [
            (
                "Native and COB have independently authenticated and converged "
                "completed-aperture angular distributions through the same stack."
            ),
            (
                "Both modes close to 2.6 umol/J at the completed aperture; "
                "therefore internal stack transmission cannot itself explain a "
                "completed-aperture PPF requirement ratio."
            ),
            (
                "Geometric active-domain rejection and authenticated GLB "
                "first-hit interception quantify source-shape-dependent losses."
            ),
            (
                "The matched Direct production runs provide an authenticated "
                "zero-bounce check of the combined source/room transport effect."
            ),
            (
                "The Direct penalty is present at -ab 0 and is therefore not "
                "caused solely by ambient-bounce transport."
            ),
            (
                "The source-mode-dependent ratio between integrated far-field "
                "rtrace response and rfluxmtx completed-aperture PPF accounts "
                "for most of the Direct discrepancy when combined with geometric "
                "acceptance; it is reported as a numerical/model response, not "
                "as authenticated physical photon loss."
            ),
        ],
        "not_yet_authenticated": [
            (
                "The two recorded native Quality metrics have no raw run trees "
                "and are historical values only."
            ),
            (
                "A fully authenticated Quality decomposition, including the "
                "incremental effect of reflected transport relative to Direct, "
                "requires rerunning the two native Quality cases later."
            ),
            (
                "No causal allocation of the historical ~10.7% gap to Quality "
                "multi-bounce transport is claimed by this audit."
            ),
        ],
    }


def _markdown(report: dict[str, object]) -> str:
    comparison = report["comparison"]
    characterization = report["matched_completed_aperture_characterization"]
    assert isinstance(comparison, dict)
    assert isinstance(characterization, dict)
    source_modes = characterization["source_modes"]
    rooms = comparison["rooms"]
    assert isinstance(source_modes, dict)
    assert isinstance(rooms, dict)
    lines = [
        "# Proposed COB versus native SMD optical audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Schema: `{report['schema_id']}` v{report['schema_version']}",
        "- Quality transport cases executed by this audit: `0`",
        "- Certified archive unchanged after revalidation: `true`",
        "",
        "## Completed-aperture characterization",
        "",
        "| Metric | Native SMD | COB surrogate |",
        "|---|---:|---:|",
    ]
    for label, path, unit in (
        ("Stack transmission", "stack_transmission", ""),
        (
            "Required internal PPE",
            "required_internal_ppe_umol_per_j",
            " µmol/J",
        ),
        (
            "Completed-aperture PPE",
            "completed_aperture_ppe_umol_per_j",
            " µmol/J",
        ),
    ):
        native = float(source_modes[NATIVE_SOURCE_MODE][path])
        cob = float(source_modes[COB_SOURCE_MODE][path])
        lines.append(
            f"| {label} | {native:.9g}{unit} | {cob:.9g}{unit} |"
        )
    for label, path, unit in (
        ("FWHM", "completed_aperture_fwhm_deg", "°"),
        ("Mean polar angle", "flux_weighted_mean_polar_angle_deg", "°"),
        ("RMS polar angle", "flux_weighted_rms_polar_angle_deg", "°"),
        ("E[cos θ]", "flux_weighted_expected_cos_theta", ""),
        ("E[cos² θ]", "flux_weighted_expected_cos2_theta", ""),
    ):
        native = float(source_modes[NATIVE_SOURCE_MODE]["metrics"][path])
        cob = float(source_modes[COB_SOURCE_MODE]["metrics"][path])
        lines.append(
            f"| {label} | {native:.9g}{unit} | {cob:.9g}{unit} |"
        )
    lines.extend(
        [
            "",
            "Both modes are normalized independently to 2.6 µmol/J at the "
            "completed aperture. The higher COB stack transmission lowers its "
            "required internal PPE; it does not create a completed-aperture PPF "
            "penalty.",
            "",
            "## Room-level diagnostic ratios",
            "",
            "| Room | Geometric | Geometric + body | Trace response + geometric + body | Direct | Historical Quality |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for room_id, label in (
        ("10x10_aisle_off", "10×10 ft, Aisle off"),
        ("30x50_aisle_on", "30×50 ft, Aisle on"),
    ):
        room = rooms[room_id]
        lines.append(
            "| "
            + label
            + " | "
            + f"{float(room['geometric_acceptance_required_ppf_ratio_cob_to_native']):.6f}"
            + " | "
            + f"{float(room['geometric_plus_independent_body_factor_ratio_cob_to_native']):.6f}"
            + " | "
            + f"{float(room['angular_trace_yield_plus_geometric_plus_body_ratio_cob_to_native']):.6f}"
            + " | "
            + f"{float(room['direct_required_ppf_ratio_cob_to_native']):.6f}"
            + " | "
            + f"{float(room['historical_quality_ppf_ratio_cob_to_native']):.6f}"
            + " |"
        )
    lines.extend(
        [
            "",
            "The geometric result uses a center-launched far-field "
            "distribution and therefore lacks full aperture position-angle "
            "phase space. The GLB-body factor is an independent first-hit "
            "quadrature, so multiplying the two is a diagnostic comparison, "
            "not an exact factorization.",
            "",
            "## Evidence boundary",
            "",
            "The certified COB Quality trees authenticate successfully. The "
            "recorded native Quality values are historical comparison values "
            "only because their raw artifacts are unavailable. No Quality case "
            "was rerun.",
            "",
            "A fully authenticated decomposition of the historical Quality gap "
            "requires two later native Quality runs. In particular, this audit "
            "does not assign any residual between Direct and the historical "
            "Quality ratio to reflected transport as a proven cause.",
            "",
            "## Quadrature formulas",
            "",
            "Angular flux uses each raw θ/φ cell as "
            "`mean(I00,I01,I10,I11) × 4 Δφ × (cos θ0 − cos θ1)`. Moments use "
            "the same endpoint cell quadrature on `I g(θ)`. Receiver acceptance "
            "tests all four square-symmetry quadrants against the production "
            "active rectangle. Fixture interception uses 15° polar rings, 15° "
            "azimuth cells, exact cell solid angle, and a 3×3 aperture-position "
            "quadrature.",
            "",
            "The far-field `rtrace` response integrates to a different fraction "
            "of the independent `rfluxmtx` aperture PPF for the two source "
            "modes. That source-mode trace-response ratio explains most of the "
            "Direct penalty when combined with geometric acceptance. It is a "
            "solver/model diagnostic and is not relabeled as physical stack "
            "loss.",
            "",
        ]
    )
    return "\n".join(lines)


def execute(output: Path, archive: Path) -> Path:
    root = output.expanduser().resolve()
    if root.exists() or root.is_symlink():
        raise ProposedCobAuditError(
            f"audit output must not already exist: {root}"
        )
    root.mkdir(parents=True)
    archive_auth = authenticate_certified_archive(
        archive, root / "certified-archive-validation"
    )
    _write_json(root / "archive-authentication.v1.json", archive_auth)

    matched = execute_matched_characterization(
        root / "matched-characterization", quality="quality"
    )
    characterization = matched["report"]
    grids = matched["grids"]
    assert isinstance(characterization, dict)
    assert isinstance(grids, dict)
    acceptance = evaluate_receiver_acceptance(grids)
    _write_json(root / "receiver-acceptance.v1.json", acceptance)
    interception = execute_fixture_interception(
        root / "fixture-interception", grids
    )
    direct = execute_direct_cases(
        root / "direct",
        execution_directory=(
            Path("/tmp")
            / "fspm-optics-proposed-cob-direct-final-v1-20260729"
        ),
    )
    comparison = _comparison_payload(
        characterization=characterization,
        acceptance=acceptance,
        interception=interception,
        direct=direct,
    )
    git = {
        "head": _run_text("git", "rev-parse", "HEAD"),
        "branch": _run_text("git", "branch", "--show-current"),
        "status_short": _run_text("git", "status", "--short"),
        "diff_stat": _run_text("git", "diff", "--stat"),
    }
    provenance = {
        "repository": str(REPOSITORY_ROOT),
        "git": git,
        "python": sys.version,
        "commands": [
            (
                "python scripts/audit_proposed_cob_comparison.py "
                f"--archive {archive.resolve()} --output-dir {root}"
            ),
            "python -m fspm_optics.diagnostics.stage_a_validator RUN --output-dir OUT",
            "pytest -q tests/test_proposed_cob_audit.py tests/test_proposed_cob_mode.py",
            "pytest -q",
            "npm run test:viewer",
        ],
        "configuration": {
            "quality_transport_cases_executed": 0,
            "matched_angular_quality": "quality",
            "matched_angular_nthreads": 1,
            "direct_quality": "direct",
            "target_mean_ppfd_umol_m2_s": 750.0,
            "mounting_height_in": 18.0,
            "analysis_scope": "baseline_ppfd",
            "control_mode": "uniform_module_dimming",
            "basis_matrix_solver_enabled": False,
            "post_trace_field_scaling": False,
        },
    }
    report = artifact_with_identity(
        {
            "schema_id": AUDIT_SCHEMA_ID,
            "schema_version": AUDIT_SCHEMA_VERSION,
            "status": "pass",
            "non_production_diagnostic": True,
            "quality_transport_cases_executed": 0,
            "archive_authentication": archive_auth,
            "source_identities": current_source_identities(),
            "matched_completed_aperture_characterization": characterization,
            "receiver_acceptance": acceptance,
            "fixture_interception": interception,
            "matched_direct": direct,
            "comparison": comparison,
            "provenance": provenance,
            "checks": {
                "certified_archive_unchanged": True,
                "certified_stage_a_revalidation_pass": True,
                "source_identities_pass": True,
                "angular_convergence_pass": True,
                "completed_aperture_ppf_closure_pass": True,
                "direct_validators_pass": True,
                "direct_pair_invariants_pass": True,
                "quality_rerun_absent": True,
            },
        }
    )
    _write_json(root / "proposed-cob-optical-audit.v1.json", report)
    atomic_write_text(root / "SUMMARY.md", _markdown(report))

    # The inventory is generated only after every scientific artifact is final.
    # Like standard checksum manifests, it explicitly excludes itself.
    inventory = inventory_tree(root)
    inventory_payload = inventory.to_dict() | {
        "scope_note": (
            "complete audit-tree inventory before this inventory file was "
            "created; the checksum manifest necessarily excludes itself"
        )
    }
    _write_json(root / "sha256-inventory.v1.json", inventory_payload)
    atomic_write_text(
        root / "sha256-inventory.v1.sha256",
        sha256_file(root / "sha256-inventory.v1.json")
        + "  sha256-inventory.v1.json\n",
    )
    return root / "proposed-cob-optical-audit.v1.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    arguments = parser.parse_args()
    try:
        report = execute(arguments.output_dir, arguments.archive)
    except (OSError, RuntimeError, ValueError, ProposedCobAuditError) as exc:
        print(f"Proposed COB comparison audit failed: {exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
