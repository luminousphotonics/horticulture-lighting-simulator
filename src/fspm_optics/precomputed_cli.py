"""Fail-closed CLI for plan, status, verification, and explicit execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.precomputed.fixed_plan import (
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
    FixedSweepPlan,
    build_fixed_sweep_plan,
)
from fspm_optics.precomputed.committed_playback import (
    build_committed_playback_plan,
    inspect_committed_playback_catalog,
)
from fspm_optics.precomputed.sweep import (
    BoundedNativeSweepExecutor,
    execute_fixed_sweep,
    inspect_fixed_sweep,
    preflight_fixed_sweep_execution,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly execute the fixed 24-case compact sweep."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser(
        "plan", help="print the deterministic plan; never starts native work"
    )
    plan.add_argument(
        "--output-root",
        type=Path,
        help="also report freshly validated bundle status without writing",
    )
    plan.add_argument("--json", action="store_true", help="emit machine JSON")

    status = subparsers.add_parser(
        "status", help="freshly validate every expected compact bundle"
    )
    status.add_argument("--output-root", type=Path, required=True)
    status.add_argument("--json", action="store_true", help="emit machine JSON")

    verify = subparsers.add_parser(
        "verify", help="require all 24 compact bundles to validate exactly"
    )
    verify.add_argument("--output-root", type=Path, required=True)
    verify.add_argument("--json", action="store_true", help="emit machine JSON")

    execute = subparsers.add_parser(
        "execute", help="run missing/invalid cases through the bounded native worker"
    )
    execute.add_argument("--output-root", type=Path, required=True)
    execute.add_argument("--runtime-root", type=Path, required=True)
    execute.add_argument(
        "--authorize-native-execution",
        action="store_true",
        help="required acknowledgement that native Radiance work will start",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "plan":
        plan = build_fixed_sweep_plan()
        status = (
            inspect_fixed_sweep(arguments.output_root, plan=plan)
            if arguments.output_root is not None
            else None
        )
        if arguments.json:
            payload = plan.to_payload()
            if status is not None:
                payload["bundle_status"] = status.to_payload()
            print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
        else:
            _print_plan(plan, status=status)
        return 0
    if arguments.command in {"status", "verify"}:
        plan = build_committed_playback_plan()
        status = inspect_committed_playback_catalog(
            arguments.output_root, plan=plan
        )
        if arguments.json:
            print(
                json.dumps(
                    status.to_payload(), indent=2, sort_keys=True, allow_nan=False
                )
            )
        else:
            _print_status(status.to_payload())
        if arguments.command == "verify" and status.valid_count != plan.case_count:
            return 1
        return 0
    if arguments.command == "execute":
        plan = build_fixed_sweep_plan()
        if not arguments.authorize_native_execution:
            print(
                "Refusing native execution: --authorize-native-execution is required.",
                file=sys.stderr,
            )
            return 2
        repository_root = _repository_root()
        preflight = preflight_fixed_sweep_execution(
            arguments.output_root,
            repository_root=repository_root,
            plan=plan,
        )
        print(
            "Authorized fixed sweep: "
            f"plan={preflight['plan_identity_sha256']} cases=24 concurrency=1",
            flush=True,
        )
        with BoundedNativeSweepExecutor(
            runtime_root=arguments.runtime_root,
            repository_root=repository_root,
        ) as executor:
            result = execute_fixed_sweep(
                arguments.output_root,
                executor,
                plan=plan,
                progress_sink=_print_progress,
            )
        print(
            f"Fixed sweep complete: valid={result.final_status.valid_count}/24",
            flush=True,
        )
        return 0
    raise AssertionError(f"unhandled command: {arguments.command}")


def _print_plan(plan: FixedSweepPlan, *, status: object | None = None) -> None:
    status_by_case: dict[str, str] = {}
    if status is not None:
        status_by_case = {
            item.case.case_id: item.validation.status.value  # type: ignore[attr-defined]
            for item in status.cases  # type: ignore[attr-defined]
        }
    print(f"Plan identity: {plan.plan_identity_sha256}")
    print(f"Canonical cases: {plan.case_count}")
    print(
        "Fixed target: "
        f"{FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:g} µmol/m²/s "
        "(all 24 cases)"
    )
    print(
        "#  system        variant                    room     aisle  "
        "case ID                                             status/output"
    )
    for case in plan.cases:
        aligned = case.canonical_domain["canonical_aligned_room"]
        assert isinstance(aligned, dict)
        room = f"{int(aligned['length_x_ft'])}x{int(aligned['width_y_ft'])}"
        suffix = status_by_case.get(case.case_id, "not inspected")
        print(
            f"{case.ordinal:02d} {case.system_id:<13} {case.layout_variant:<26} "
            f"{room:<8} {'on' if case.aisle_enabled else 'off':<6} "
            f"target={FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S:g} µmol/m²/s "
            f"{case.case_id} {suffix} -> {case.output_relative_path}"
        )
        orders = case.canonical_domain["supported_requested_room_orders_ft"]
        print(
            "   requested orders: "
            + ", ".join(
                f"{item['length']:g}x{item['width']:g}"  # type: ignore[index,union-attr]
                for item in orders
            )
        )
        print(
            "   resolved: "
            + json.dumps(
                case.resolved_configuration,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


def _print_status(payload: dict[str, object]) -> None:
    print(f"Plan identity: {payload['plan_identity_sha256']}")
    print(f"Output root: {payload['output_root']}")
    print(
        "Valid/skipped: "
        f"{payload['valid_or_skipped_cases']}/{payload['total_cases']}; "
        f"missing: {payload['missing_or_pending_cases']}; "
        f"invalid: {payload['invalid_or_incompatible_cases']}; "
        f"remaining: {payload['remaining_cases']}"
    )
    for case in payload["cases"]:  # type: ignore[union-attr]
        print(
            f"{case['ordinal']:02d} {case['status']} {case['case_id']} "  # type: ignore[index]
            f"-> {case['path']}"  # type: ignore[index]
        )


def _print_progress(
    event: str,
    case: object | None,
    data: object,
) -> None:
    case_id = "plan" if case is None else case.case_id  # type: ignore[union-attr]
    print(f"{event} {case_id} {json.dumps(data, sort_keys=True)}", flush=True)


def _repository_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".git").exists():
            return parent
    raise RuntimeError("unable to locate the fspm-optics repository root.")


if __name__ == "__main__":
    raise SystemExit(main())
