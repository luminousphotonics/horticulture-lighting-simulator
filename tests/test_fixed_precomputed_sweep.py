from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from fspm_optics.precomputed import sweep
from fspm_optics.precomputed.compact_bundle import (
    CompactBundleExport,
    CompactBundleStatus,
    CompactBundleValidation,
)
from fspm_optics.precomputed.fixed_plan import FixedSweepCase, build_fixed_sweep_plan


@dataclass
class FakeCompactWorld:
    runtime_root: Path
    states: dict[Path, CompactBundleStatus] = field(default_factory=dict)
    executor_calls: list[str] = field(default_factory=list)
    export_calls: list[str] = field(default_factory=list)

    def validator(
        self,
        path: str | Path,
        *,
        expected_run_configuration: object | None = None,
        expected_authenticated_identities: object | None = None,
    ) -> CompactBundleValidation:
        assert expected_run_configuration is not None
        assert expected_authenticated_identities is not None
        fixed = expected_run_configuration["fixed_plan"]  # type: ignore[index]
        request = expected_run_configuration["request"]  # type: ignore[index]
        fixed_inputs = expected_authenticated_identities[  # type: ignore[index]
            "fixed_plan_inputs"
        ]["compatibility_inputs"]
        assert fixed["target_ppfd_umol_m2_s"] == 250.0
        assert request["quality"] == "standard"
        assert fixed_inputs["solver"]["radiance_quality"] == "standard"
        assert "ppfd-250" in fixed["case_id"]
        status = self.states.get(Path(path), CompactBundleStatus.MISSING)
        return CompactBundleValidation(
            status,
            f"fake {status.value}",
            "f" * 64 if status is CompactBundleStatus.VALID else None,
        )

    def executor(self, case: FixedSweepCase) -> Path:
        self.executor_calls.append(case.case_id)
        runtime = self.runtime_root / case.case_id
        runtime.mkdir(parents=True)
        return runtime

    def exporter(
        self,
        completed_runtime: str | Path,
        destination: str | Path,
        *,
        fixed_case_binding: object,
        fixed_plan_inputs: object,
    ) -> CompactBundleExport:
        del completed_runtime
        binding = fixed_case_binding  # type: ignore[assignment]
        assert binding["target_ppfd_umol_m2_s"] == 250.0
        assert fixed_plan_inputs["solver"]["radiance_quality"] == "standard"
        assert binding["target_semantics"] in {
            "requested_mean_target",
            "fixed_output_comparison_reference_no_dimming",
        }
        path = Path(destination)
        path.write_bytes(b"synthetic compact fixture; no native process")
        self.states[path] = CompactBundleStatus.VALID
        self.export_calls.append(binding["case_id"])
        return CompactBundleExport(path, "f" * 64, path.stat().st_size, 1)


def test_output_root_rejects_broad_and_symlinked_destinations(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="broad or protected"):
        sweep.validate_output_root(Path("/"))
    with pytest.raises(ValueError, match="broad or protected"):
        sweep.validate_output_root(Path.home())
    repository = Path(__file__).resolve().parents[1]
    with pytest.raises(ValueError, match="broad or protected"):
        sweep.validate_output_root(repository, repository_root=repository)

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="must not be symlinks"):
        sweep.validate_output_root(link / "child")

    output = tmp_path / "output"
    output.mkdir()
    (output / "proposed").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="case output path must not contain symlinks"):
        sweep.inspect_fixed_sweep(output)


def test_execution_preflight_is_read_only_and_validates_fixed_authorities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sweep,
        "resolve_executable",
        lambda name, **_kwargs: Path("/authenticated-tools") / name,
    )
    repository = Path(__file__).resolve().parents[1]
    output = tmp_path / "not-created-by-preflight"
    report = sweep.preflight_fixed_sweep_execution(
        output,
        repository_root=repository,
    )

    assert report["case_count"] == 24
    assert report["default_concurrency"] == 1
    assert set(report["executables"]) == {"ies2rad", "oconv", "rtrace"}
    assert len(report["source_assets"]) == 4
    assert report["fixture_assets"]
    assert not output.exists()


def test_fake_executor_covers_all_resume_classifications_and_24_case_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = build_fixed_sweep_plan()
    output = tmp_path / "precomputed"
    world = FakeCompactWorld(tmp_path / "fake-runtimes")
    initial_statuses = (
        CompactBundleStatus.VALID,
        CompactBundleStatus.PARTIAL,
        CompactBundleStatus.CORRUPT,
        CompactBundleStatus.INCOMPATIBLE_SCHEMA,
        CompactBundleStatus.CONFIGURATION_MISMATCH,
        CompactBundleStatus.IDENTITY_MISMATCH,
    )
    for case, status in zip(
        plan.cases[: len(initial_statuses)], initial_statuses, strict=True
    ):
        world.states[case.output_path(output)] = status
    monkeypatch.setattr(sweep, "validate_compact_bundle", world.validator)

    inspected = sweep.inspect_fixed_sweep(output, plan=plan)
    assert inspected.valid_count == 1
    assert inspected.invalid_count == 5
    assert inspected.remaining_count == 23
    assert not output.exists()

    result = sweep.execute_fixed_sweep(
        output,
        world.executor,
        plan=plan,
        exporter=world.exporter,
    )
    assert result.final_status.valid_count == 24
    assert result.skipped_case_ids == (plan.cases[0].case_id,)
    assert result.executed_case_ids == tuple(case.case_id for case in plan.cases[1:])
    assert world.executor_calls == list(result.executed_case_ids)
    assert world.export_calls == list(result.executed_case_ids)
    progress = json.loads((output / sweep.PROGRESS_FILENAME).read_text())
    assert progress["plan_identity_sha256"] == plan.plan_identity_sha256
    assert progress["validated_completed_cases"] == 24
    assert progress["executed_completed_cases"] == 23
    assert progress["failed_cases"] == 0
    assert progress["remaining_cases"] == 0

    world.executor_calls.clear()
    world.export_calls.clear()
    resumed = sweep.execute_fixed_sweep(
        output,
        world.executor,
        plan=plan,
        exporter=world.exporter,
    )
    assert resumed.final_status.valid_count == 24
    assert resumed.executed_case_ids == ()
    assert len(resumed.skipped_case_ids) == 24
    assert world.executor_calls == []
    assert world.export_calls == []
    progress = json.loads((output / sweep.PROGRESS_FILENAME).read_text())
    assert progress["validated_completed_cases"] == 24
    assert progress["executed_completed_cases"] == 0
    assert progress["preexisting_or_skipped_cases"] == 24


def test_publication_is_completion_authority_when_progress_sink_interrupts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = build_fixed_sweep_plan()
    output = tmp_path / "precomputed"
    world = FakeCompactWorld(tmp_path / "fake-runtimes")
    monkeypatch.setattr(sweep, "validate_compact_bundle", world.validator)
    published_case: list[str] = []

    def interrupt_after_publication(
        event: str,
        case: FixedSweepCase | None,
        _data: object,
    ) -> None:
        if event == "case.published" and not published_case:
            assert case is not None
            published_case.append(case.case_id)
            raise KeyboardInterrupt("synthetic power loss after atomic publication")

    with pytest.raises(KeyboardInterrupt, match="synthetic power loss"):
        sweep.execute_fixed_sweep(
            output,
            world.executor,
            plan=plan,
            exporter=world.exporter,
            progress_sink=interrupt_after_publication,
        )
    assert published_case == [plan.cases[0].case_id]
    assert world.states[plan.cases[0].output_path(output)] is CompactBundleStatus.VALID
    interrupted_progress = json.loads(
        (output / sweep.PROGRESS_FILENAME).read_text()
    )
    assert interrupted_progress["status"]["valid_or_skipped_cases"] == 1

    world.executor_calls.clear()
    world.export_calls.clear()
    resumed = sweep.execute_fixed_sweep(
        output,
        world.executor,
        plan=plan,
        exporter=world.exporter,
    )
    assert resumed.final_status.valid_count == 24
    assert plan.cases[0].case_id in resumed.skipped_case_ids
    assert plan.cases[0].case_id not in resumed.executed_case_ids
    assert resumed.executed_case_ids == tuple(case.case_id for case in plan.cases[1:])


def test_interruption_before_export_reexecutes_only_the_unpublished_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = build_fixed_sweep_plan()
    output = tmp_path / "precomputed"
    world = FakeCompactWorld(tmp_path / "fake-runtimes")
    for case in plan.cases[1:]:
        world.states[case.output_path(output)] = CompactBundleStatus.VALID
    monkeypatch.setattr(sweep, "validate_compact_bundle", world.validator)
    interrupted = False

    def interrupt_once(case: FixedSweepCase) -> Path:
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            world.executor_calls.append(case.case_id)
            raise KeyboardInterrupt("synthetic interruption before compact export")
        return world.executor(case)

    with pytest.raises(KeyboardInterrupt, match="before compact export"):
        sweep.execute_fixed_sweep(
            output,
            interrupt_once,
            plan=plan,
            exporter=world.exporter,
        )
    assert world.states.get(plan.cases[0].output_path(output)) is None

    world.executor_calls.clear()
    resumed = sweep.execute_fixed_sweep(
        output,
        interrupt_once,
        plan=plan,
        exporter=world.exporter,
    )
    assert resumed.final_status.valid_count == 24
    assert resumed.executed_case_ids == (plan.cases[0].case_id,)
    assert world.executor_calls[0] == plan.cases[0].case_id
