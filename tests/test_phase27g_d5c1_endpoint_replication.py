from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fspm_optics.application import surface_flux_endpoint_replication as replication
from fspm_optics.application.surface_flux_recalibration import D5_BAND_ORDER
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)


CREATED_AT = "2026-07-16T12:00:00Z"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _installation() -> RadianceInstallation:
    return RadianceInstallation(
        oconv=RadianceExecutableVersion(
            name="oconv",
            path=Path("/test-radiance/bin/oconv"),
            version_text="mock oconv",
        ),
        rtrace=RadianceExecutableVersion(
            name="rtrace",
            path=Path("/test-radiance/bin/rtrace"),
            version_text="mock rtrace",
        ),
    )


class _NoRunner:
    def run(self, command, *, timeout_s=None, stderr_path=None):
        del command, timeout_s, stderr_path
        raise AssertionError("bounded D5-C1 fake must not execute a native command")


def _original_command(family: str, band_id: str) -> dict[str, object]:
    return {
        "oconv": {
            "argv": ["/test-radiance/bin/oconv", "-f", "source.rad"],
            "cwd": ".",
            "environment": {},
            "label": f"original_{band_id}_oconv",
            "shell": False,
            "stdin_path": None,
            "stdout_path": "scene.oct",
        },
        "rtrace": {
            "argv": [
                "/test-radiance/bin/rtrace",
                "-h",
                "-I+",
                "-n",
                "6",
                *radiance_options(family),
                "-af",
                f"bands/{band_id}/scene.amb",
                "scene.oct",
            ],
            "cwd": ".",
            "environment": {},
            "label": f"original_{band_id}_rtrace",
            "shell": False,
            "stdin_path": "scene/receivers.pts",
            "stdout_path": "receiver.rgb",
        },
    }


def _fake_authenticated(
    tmp_path: Path,
    *,
    receiver_count: int = 2,
) -> tuple[
    replication._AuthenticatedInputs,
    Path,
    Path,
    Path,
]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    a1_root = tmp_path / "a1"
    a2_root = tmp_path / "a2"
    output = tmp_path / "c1"
    a1_root.mkdir()
    a2_root.mkdir()
    installation = _installation()
    receiver_bytes = b"\0" * (receiver_count * 8)
    sources = []
    for a1_order_index, job in enumerate(replication.endpoint_replication_jobs()):
        job_root = a1_root / "jobs" / job.job_id
        bands = []
        total_amplitude = job.requested_level / 3.0
        band_amplitude = total_amplitude / 4.0
        for band_order_index, band_id in enumerate(D5_BAND_ORDER):
            relative = (
                f"bands/{band_order_index:02d}-{band_id}/"
                "receiver-values.v1.f64le.bin"
            )
            receiver_path = job_root / relative
            receiver_path.parent.mkdir(parents=True, exist_ok=True)
            receiver_path.write_bytes(receiver_bytes)
            bands.append(
                {
                    "order_index": band_order_index,
                    "band_id": band_id,
                    "source": {"rendered_amplitude": band_amplitude},
                    "source_sha256": "1" * 64,
                    "material_sha256": "2" * 64,
                    "receiver_values": {
                        "role": f"{band_id}_receiver_values",
                        "path": relative,
                        "media_type": "application/octet-stream",
                        "byte_length": len(receiver_bytes),
                        "sha256": _sha(receiver_bytes),
                        "row_count": receiver_count,
                        "stride_bytes": 8,
                    },
                    "commands": _original_command(job.quality, band_id),
                }
            )
        sources.append(
            replication._SourceJob(
                job=job,
                a1_order_index=a1_order_index,
                a1_job_root=job_root,
                threads=6,
                original_radiance_installation=installation.to_dict(),
                achieved_reference=job.requested_level + 0.125,
                total_source_amplitude=total_amplitude,
                band_source_amplitude=band_amplitude,
                bands=tuple(bands),
            )
        )
    a1 = SimpleNamespace(
        input_root=a1_root,
        configuration=SimpleNamespace(
            threads=6,
            oconv_command="oconv",
            rtrace_command="rtrace",
        ),
        configuration_identity={"radiance_installation": installation.to_dict()},
        completion={
            "configuration_sha256": "a" * 64,
            "created_at_utc": CREATED_AT,
        },
        completion_sha256="b" * 64,
        scene=SimpleNamespace(),
        results=(),
    )
    authenticated_a2 = replication._AuthenticatedA2(
        root=a2_root,
        completion={"promotion_eligible": False},
        completion_sha256="c" * 64,
        report={},
        report_sha256="d" * 64,
    )
    return (
        replication._AuthenticatedInputs(
            a1=a1,
            a2=authenticated_a2,
            source_jobs=tuple(sources),
        ),
        a1_root,
        a2_root,
        output,
    )


def _repeated_commands(
    family: str, installation: RadianceInstallation
) -> dict[str, object]:
    return {
        "oconv": {
            "label": f"repeat_{family}_oconv",
            "argv": [
                str(installation.oconv.path),
                "-f",
                "scene-boundary.rad",
                "source.rad",
                "leaf-material.rad",
                "scene/plant-geometry.rad",
            ],
            "stdin_path": None,
            "stdout_path": "scene.oct",
            "cwd": ".",
            "environment": {},
            "shell": False,
        },
        "rtrace": {
            "label": f"repeat_{family}_rtrace",
            "argv": [
                str(installation.rtrace.path),
                "-h",
                "-I+",
                "-n",
                "6",
                *radiance_options(family),
                "-af",
                "scene.amb",
                "scene.oct",
            ],
            "stdin_path": "scene/receivers.pts",
            "stdout_path": "receiver.rgb",
            "cwd": ".",
            "environment": {},
            "shell": False,
        },
    }


def _fake_trace_factory(
    *,
    identical_sentinel: bool,
    calls: list[int],
    fail_once_at: int | None = None,
):
    state = {"failed": False}

    def execute(
        trace_root,
        *,
        source,
        original_band,
        band_id,
        band_order_index,
        trace_order_index,
        scene,
        material_plan,
        threads,
        runner,
        installation,
    ):
        del scene, material_plan, runner
        assert threads == 6
        calls.append(trace_order_index)
        if (
            fail_once_at is not None
            and trace_order_index == fail_once_at
            and not state["failed"]
        ):
            state["failed"] = True
            raise RuntimeError("bounded interruption")
        trace_root.mkdir(parents=True)
        original = original_band["receiver_values"]
        original_path = source.a1_job_root / str(original["path"])
        if trace_order_index == 0 and identical_sentinel:
            data = original_path.read_bytes()
        else:
            data = bytes([trace_order_index + 1]) * int(original["byte_length"])
        values_path = trace_root / "receiver-values.v1.f64le.bin"
        values_path.write_bytes(data)
        workspace = f"traces/{band_order_index:02d}-{band_id}"
        repeated_receiver = {
            "role": f"{band_id}_receiver_values",
            "path": f"{workspace}/receiver-values.v1.f64le.bin",
            "media_type": "application/octet-stream",
            "byte_length": len(data),
            "sha256": _sha(data),
            "row_count": len(data) // 8,
            "stride_bytes": 8,
        }
        identical = data == original_path.read_bytes()
        return {
            "schema_id": replication.D5_C1_TRACE_SCHEMA_ID,
            "schema_version": replication.D5_C1_SCHEMA_VERSION,
            "experiment_id": replication.D5_C1_EXPERIMENT_ID,
            "repetition_id": replication.D5_C1_REPETITION_ID,
            "trace_order_index": trace_order_index,
            "job_id": source.job.job_id,
            "family": source.job.quality,
            "requested_reference_level_umol_m2_s": source.job.requested_level,
            "band_order_index": band_order_index,
            "band_id": band_id,
            "sentinel": trace_order_index == 0,
            "status": "complete",
            "stage_a_trace_count": 0,
            "achieved_reference_R_umol_m2_s": source.achieved_reference,
            "resolved_total_source_radiance_amplitude": source.total_source_amplitude,
            "rendered_band_source_amplitude": source.band_source_amplitude,
            "amplitude_provenance": "authenticated D5-A1 Stage A final authority",
            "original": {
                "receiver_values": {
                    **original,
                    "path": f"jobs/{source.job.job_id}/{original['path']}",
                },
                "commands": original_band["commands"],
                "radiance_installation": installation.to_dict(),
            },
            "repeated": {
                "workspace": workspace,
                "receiver_values": repeated_receiver,
                "commands": _repeated_commands(source.job.quality, installation),
                "radiance_installation": installation.to_dict(),
            },
            "cache": {
                **replication._cache_policy(),
                "workspace": workspace,
                "ambient_cache_path": f"{workspace}/scene.amb",
                "ambient_cache_absent_before_rtrace": True,
                "ambient_cache_present_after_rtrace": False,
            },
            "byte_identical_to_original": identical,
            "comparison": {
                "complete_float64_bytes_compared": True,
                "original_sha256": original["sha256"],
                "repeated_sha256": _sha(data),
                "sha256_identical": original["sha256"] == _sha(data),
            },
            "independence_interpretation": (
                "byte identity fails the mandatory nonidentical-repetition sentinel"
                if identical
                else "nonidentical bytes do not prove an independently seeded sample"
            ),
        }

    return execute


def _config(a1_root: Path, a2_root: Path, output: Path, *, resume=False):
    return replication.SurfaceFluxEndpointReplicationConfig(
        a1_input_directory=a1_root,
        a2_input_directory=a2_root,
        output_directory=output,
        resume=resume,
    )


def _install_bounded_execution(
    monkeypatch: pytest.MonkeyPatch,
    authenticated: replication._AuthenticatedInputs,
    execute,
) -> None:
    monkeypatch.setattr(
        replication,
        "_authenticate_inputs",
        lambda _a1, _a2: authenticated,
    )
    monkeypatch.setattr(replication, "_execute_trace", execute)
    monkeypatch.setattr(replication, "_material_plan", lambda: SimpleNamespace())
    monkeypatch.setattr(replication, "D5_EXPECTED_RECEIVER_COUNT_PER_BAND", 2)
    monkeypatch.setattr(replication, "D5_EXPECTED_RECEIVER_BYTES_PER_BAND", 16)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_plan_is_exact_quality_first_and_creates_no_output(tmp_path: Path) -> None:
    authenticated, _a1, _a2, output = _fake_authenticated(tmp_path)
    plan = replication._build_plan(authenticated, output_root=output)

    assert plan["families"] == ["quality", "standard"]
    assert [job["job_id"] for job in plan["job_order"]] == [
        "quality-250",
        "quality-500",
        "standard-250",
        "standard-500",
    ]
    assert plan["stage_a_trace_count"] == 0
    assert plan["stage_b_trace_count"] == 16
    assert [trace["band_id"] for trace in plan["trace_order"]] == list(
        D5_BAND_ORDER
    ) * 4
    assert plan["sentinel"] == {
        "trace_order_index": 0,
        "job_id": "quality-250",
        "family": "quality",
        "requested_reference_level_umol_m2_s": 250.0,
        "band_id": "blue",
        "stop_if_byte_identical_to_original": True,
        "remaining_trace_count_if_blocked": 15,
    }
    assert [item["quality"] for item in plan["quality_option_identities"]] == [
        "quality",
        "standard",
    ]
    assert plan["receiver_contract"]["plant_count"] == 64
    assert plan["receiver_contract"]["patches_per_plant"] == 192
    assert plan["receiver_contract"]["receiver_count_per_band"] == 24_576
    assert plan["scope"]["direct_present"] is False
    assert plan["scope"]["rigorous_present"] is False
    assert not output.exists()


def test_public_plan_authenticates_without_native_execution_or_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    calls = []

    def authenticate(a1, a2):
        calls.append((a1, a2))
        return authenticated

    monkeypatch.setattr(replication, "_authenticate_inputs", authenticate)
    plan = replication.build_surface_flux_endpoint_replication_plan(
        _config(a1_root, a2_root, output)
    )

    assert calls == [(a1_root.resolve(), a2_root.resolve())]
    assert plan["radiance_discovery_performed"] is False
    assert plan["radiance_execution_performed"] is False
    assert plan["output_created"] is False
    assert not output.exists()


def test_a1_and_a2_authentication_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a1_root = tmp_path / "a1"
    a2_root = tmp_path / "a2"
    a1_root.mkdir()
    a2_root.mkdir()
    monkeypatch.setattr(
        replication.a2,
        "_authenticate_completed_sweep",
        lambda _root: (_ for _ in ()).throw(RuntimeError("bad A1")),
    )
    with pytest.raises(
        replication.SurfaceFluxEndpointReplicationError,
        match="rejected the completed D5-A1",
    ):
        replication._authenticate_inputs(a1_root, a2_root)

    authenticated, *_ = _fake_authenticated(tmp_path / "second")
    bad_completion = {
        field: None for field in replication._A2_COMPLETION_FIELDS
    }
    bad_completion.update(
        {
            "schema_id": replication.D5_A2_COMPLETION_SCHEMA_ID,
            "schema_version": replication.D5_A2_COMPLETION_SCHEMA_VERSION,
            "analysis_id": replication.D5_A2_ANALYSIS_ID,
            "source_experiment_id": replication.D5_EXPERIMENT_ID,
            "source_completion_sha256": authenticated.a1.completion_sha256,
            "status": "partial",
            "promotion_eligible": False,
        }
    )
    with pytest.raises(
        replication.SurfaceFluxEndpointReplicationError,
        match="required completed v1 authority",
    ):
        replication._validate_a2_completion(bad_completion, authenticated.a1)


def test_identical_sentinel_publishes_blocked_and_stops_after_one_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    calls: list[int] = []
    execute = _fake_trace_factory(identical_sentinel=True, calls=calls)
    _install_bounded_execution(monkeypatch, authenticated, execute)
    before_a1 = _snapshot(a1_root)
    before_a2 = _snapshot(a2_root)

    publication = replication.run_surface_flux_endpoint_replication(
        _config(a1_root, a2_root, output),
        runner=_NoRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=tmp_path,
    )

    assert publication.status == "blocked"
    assert calls == [0]
    assert publication.outcome["execution_complete"] is False
    assert publication.outcome["stage_b_trace_count_executed"] == 1
    assert publication.outcome["stage_b_trace_count_remaining"] == 15
    assert publication.outcome["variance_analysis_suitable"] is False
    assert publication.outcome["sentinel"]["byte_identical_to_original"] is True
    assert not (output / "jobs" / "quality-500").exists()
    assert _snapshot(a1_root) == before_a1
    assert _snapshot(a2_root) == before_a2


def test_nonidentical_sentinel_continues_all_sixteen_stage_b_traces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    calls: list[int] = []
    execute = _fake_trace_factory(identical_sentinel=False, calls=calls)
    _install_bounded_execution(monkeypatch, authenticated, execute)

    publication = replication.run_surface_flux_endpoint_replication(
        _config(a1_root, a2_root, output),
        runner=_NoRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=tmp_path,
    )

    assert publication.status == "complete"
    assert calls == list(range(16))
    assert publication.outcome["stage_a_trace_count"] == 0
    assert publication.outcome["stage_b_trace_count_executed"] == 16
    assert publication.outcome["sentinel"]["passed"] is True
    assert publication.outcome["independently_seeded_sample_claimed"] is False
    assert publication.outcome["nonidentical_bytes_prove_independent_seed"] is False
    assert [job["job_id"] for job in publication.outcome["ordered_jobs"]] == [
        "quality-250",
        "quality-500",
        "standard-250",
        "standard-500",
    ]


def test_exact_options_and_trace_local_cache_are_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    calls: list[int] = []
    _install_bounded_execution(
        monkeypatch,
        authenticated,
        _fake_trace_factory(identical_sentinel=False, calls=calls),
    )
    replication.run_surface_flux_endpoint_replication(
        _config(a1_root, a2_root, output),
        runner=_NoRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=tmp_path,
    )

    workspaces = []
    for job_id in (
        "quality-250",
        "quality-500",
        "standard-250",
        "standard-500",
    ):
        result = json.loads(
            (output / "jobs" / job_id / "job-result.v1.json").read_text()
        )
        family = result["job"]["quality"]
        for trace in result["ordered_traces"]:
            argv = trace["repeated"]["commands"]["rtrace"]["argv"]
            expected = [
                "/test-radiance/bin/rtrace",
                "-h",
                "-I+",
                "-n",
                "6",
                *radiance_options(family),
                "-af",
                "scene.amb",
                "scene.oct",
            ]
            assert argv == expected
            assert all(token not in argv for token in ("-u", "-u+", "-u-"))
            assert trace["cache"]["ambient_cache_absent_before_rtrace"] is True
            assert trace["cache"]["a1_cache_reused"] is False
            workspaces.append(
                (job_id, trace["cache"]["workspace"])
            )
    assert len(set(workspaces)) == 16


def test_atomic_job_resume_reauthenticates_and_skips_committed_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    calls: list[int] = []
    execute = _fake_trace_factory(
        identical_sentinel=False,
        calls=calls,
        fail_once_at=4,
    )
    _install_bounded_execution(monkeypatch, authenticated, execute)
    with pytest.raises(RuntimeError, match="bounded interruption"):
        replication.run_surface_flux_endpoint_replication(
            _config(a1_root, a2_root, output),
            runner=_NoRunner(),
            radiance_installation=_installation(),
            created_at_utc=CREATED_AT,
            repository_root=tmp_path,
        )

    assert (output / "jobs" / "quality-250" / "job-completion.v1.json").is_file()
    assert not (output / "jobs" / "quality-500").exists()
    assert not list((output / "jobs").glob(".*.tmp"))

    publication = replication.run_surface_flux_endpoint_replication(
        _config(a1_root, a2_root, output, resume=True),
        runner=_NoRunner(),
        radiance_installation=_installation(),
        repository_root=tmp_path,
    )

    assert publication.status == "complete"
    assert calls[:5] == [0, 1, 2, 3, 4]
    assert calls.count(0) == 1
    assert calls.count(1) == 1
    assert calls[-12:] == list(range(4, 16))


def test_corrupt_committed_job_is_rejected_and_completed_output_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    calls: list[int] = []
    _install_bounded_execution(
        monkeypatch,
        authenticated,
        _fake_trace_factory(identical_sentinel=False, calls=calls),
    )
    config = _config(a1_root, a2_root, output)
    first = replication.run_surface_flux_endpoint_replication(
        config,
        runner=_NoRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=tmp_path,
    )
    second = replication.run_surface_flux_endpoint_replication(
        config,
        runner=_NoRunner(),
        repository_root=tmp_path,
    )
    assert second.outcome == first.outcome
    assert calls == list(range(16))

    receiver = (
        output
        / "jobs"
        / "quality-250"
        / "traces"
        / "00-blue"
        / "receiver-values.v1.f64le.bin"
    )
    receiver.write_bytes(b"corrupt")
    with pytest.raises(
        replication.SurfaceFluxEndpointReplicationError,
        match="inventory changed",
    ):
        replication.run_surface_flux_endpoint_replication(
            _config(a1_root, a2_root, output, resume=True),
            runner=_NoRunner(),
            repository_root=tmp_path,
        )


def test_completion_inventory_and_provenance_are_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    authenticated, a1_root, a2_root, output = _fake_authenticated(tmp_path)
    _install_bounded_execution(
        monkeypatch,
        authenticated,
        _fake_trace_factory(identical_sentinel=False, calls=[]),
    )
    publication = replication.run_surface_flux_endpoint_replication(
        _config(a1_root, a2_root, output),
        runner=_NoRunner(),
        radiance_installation=_installation(),
        created_at_utc=CREATED_AT,
        repository_root=tmp_path,
    )
    outcome = publication.outcome

    assert outcome["source_authorities"]["d5_a1"]["completion_sha256"] == "b" * 64
    assert outcome["source_authorities"]["d5_a2"]["report_sha256"] == "d" * 64
    assert outcome["source_authorities"]["d5_a2"]["completion_sha256"] == "c" * 64
    assert outcome["production_calibration_resource_generated"] is False
    assert outcome["promotion_claimed"] is False
    assert outcome["a1_input_modified"] is False
    assert outcome["a2_input_modified"] is False
    paths = [item["path"] for item in outcome["ordered_artifact_inventory"]]
    assert paths == sorted(paths)
    assert replication.D5_C1_OUTCOME_NAME not in paths
    assert all(item["sha256"] for item in outcome["ordered_artifact_inventory"])


def test_cli_contract_exposes_plan_only_resume_and_fixed_defaults() -> None:
    from fspm_optics.application.surface_flux_endpoint_replication_cli import (
        build_parser,
        config_from_namespace,
    )

    parser = build_parser()
    arguments = parser.parse_args(["--plan-only"])
    config = config_from_namespace(arguments)

    assert arguments.plan_only is True
    assert arguments.resume is False
    assert config.a1_input_directory == replication.D5_C1_DEFAULT_A1_INPUT_DIRECTORY
    assert config.a2_input_directory == replication.D5_C1_DEFAULT_A2_INPUT_DIRECTORY
    assert config.output_directory == replication.D5_C1_DEFAULT_OUTPUT_DIRECTORY
    with pytest.raises(SystemExit):
        parser.parse_args(["--plan-only", "--resume"])
