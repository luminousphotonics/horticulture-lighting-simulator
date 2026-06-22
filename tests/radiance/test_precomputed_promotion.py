from __future__ import annotations

import gzip
import json
import multiprocessing as mp
import multiprocessing.context
import tempfile
import unittest
from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.config import MODE_COMPETITOR  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precompute_sweep  # noqa: E402
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    SCHEMA_VERSION,
)


def _write_competitor_bundle(bundle_dir: Path, label: str) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(bundle_dir / "ppfd_map.txt.gz", "wt", encoding="utf-8") as handle:
        handle.write("0 0 0 100\n")
    (bundle_dir / "layout.json").write_text(
        json.dumps({"fixtures": [], "label": label}), encoding="utf-8"
    )
    (bundle_dir / "power.json").write_text(
        json.dumps({"model_label": label, "total_ppf": 100.0, "total_w": 50.0}),
        encoding="utf-8",
    )
    (bundle_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "mode": MODE_COMPETITOR,
                "artifacts": {
                    "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                    "layout_json": "layout.json",
                    "power_json": "power.json",
                },
            }
        ),
        encoding="utf-8",
    )


def _bundle_label(bundle_dir: Path) -> str:
    data = json.loads((bundle_dir / "power.json").read_text(encoding="utf-8"))
    return str(data["model_label"])


def _promote_in_process(
    candidate: str, target: str, transaction_id: str, queue: "mp.Queue[tuple[str, str]]"
) -> None:
    try:
        precompute_sweep._replace_bundle_dir(
            Path(candidate), Path(target), transaction_id=transaction_id
        )
    except Exception as exc:  # pragma: no cover - reported to parent process.
        queue.put(("error", repr(exc)))
        return
    queue.put(("ok", transaction_id))


def _safe_multiprocessing_context() -> (
    multiprocessing.context.SpawnContext | multiprocessing.context.ForkServerContext
):
    methods = mp.get_all_start_methods()
    if "spawn" in methods:
        return mp.get_context("spawn")
    if "forkserver" in methods:
        return mp.get_context("forkserver")
    raise unittest.SkipTest(
        "spawn and forkserver multiprocessing contexts are unavailable"
    )


class PrecomputedBundlePromotionTests(unittest.TestCase):
    def test_successful_replacement_promotes_candidate_and_removes_journal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_promotion_success_") as tmp:
            root = Path(tmp)
            target = root / "10x10"
            paths = precompute_sweep._bundle_promotion_paths(target, "tx-success")
            _write_competitor_bundle(target, "old")
            _write_competitor_bundle(paths.candidate, "new")

            precompute_sweep._replace_bundle_dir(
                paths.candidate, target, transaction_id=paths.transaction_id
            )

            self.assertEqual(_bundle_label(target), "new")
            self.assertFalse(paths.candidate.exists())
            self.assertFalse(paths.backup.exists())
            self.assertFalse(paths.journal.exists())

    def test_invalid_candidate_leaves_old_target_untouched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_promotion_invalid_") as tmp:
            root = Path(tmp)
            target = root / "10x10"
            paths = precompute_sweep._bundle_promotion_paths(target, "tx-invalid")
            _write_competitor_bundle(target, "old")
            paths.candidate.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "invalid precomputed bundle"):
                precompute_sweep._replace_bundle_dir(
                    paths.candidate, target, transaction_id=paths.transaction_id
                )

            self.assertEqual(_bundle_label(target), "old")
            self.assertTrue(paths.candidate.exists())
            self.assertFalse(paths.journal.exists())

    def test_recovery_after_old_target_preservation_restores_backup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_promotion_restore_") as tmp:
            root = Path(tmp)
            target = root / "10x10"
            paths = precompute_sweep._bundle_promotion_paths(target, "tx-restore")
            _write_competitor_bundle(paths.backup, "old")
            _write_competitor_bundle(paths.candidate, "new")
            precompute_sweep._write_bundle_promotion_journal(paths, "backup_preserved")

            precompute_sweep._recover_journaled_bundle_replacement(target)

            self.assertEqual(_bundle_label(target), "old")
            self.assertTrue(paths.candidate.exists())
            self.assertFalse(paths.backup.exists())
            self.assertFalse(paths.journal.exists())

    def test_recovery_after_new_target_promotion_retains_promoted_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_promotion_retain_") as tmp:
            root = Path(tmp)
            target = root / "10x10"
            paths = precompute_sweep._bundle_promotion_paths(target, "tx-retain")
            _write_competitor_bundle(target, "new")
            _write_competitor_bundle(paths.backup, "old")
            precompute_sweep._write_bundle_promotion_journal(paths, "target_promoted")

            precompute_sweep._recover_journaled_bundle_replacement(target)

            self.assertEqual(_bundle_label(target), "new")
            self.assertFalse(paths.backup.exists())
            self.assertFalse(paths.journal.exists())

    def test_failure_injection_at_each_state_transition_is_recoverable(self) -> None:
        transitions = (
            "candidate_validated",
            "prepared",
            "backup_preserved",
            "target_promoted",
            "promoted_validated",
        )
        for transition in transitions:
            with self.subTest(transition=transition):
                with tempfile.TemporaryDirectory(
                    prefix=f"rad_rebuild_promotion_{transition}_"
                ) as tmp:
                    root = Path(tmp)
                    target = root / "10x10"
                    paths = precompute_sweep._bundle_promotion_paths(
                        target, f"tx-{transition}"
                    )
                    _write_competitor_bundle(target, "old")
                    _write_competitor_bundle(paths.candidate, "new")

                    def fail_at(state: str) -> None:
                        if state == transition:
                            raise RuntimeError(f"stop at {transition}")

                    with self.assertRaisesRegex(RuntimeError, f"stop at {transition}"):
                        precompute_sweep._replace_bundle_dir(
                            paths.candidate,
                            target,
                            transaction_id=paths.transaction_id,
                            hook=fail_at,
                        )

                    precompute_sweep._recover_journaled_bundle_replacement(target)
                    self.assertTrue(precompute_sweep._bundle_dir_is_valid(target))
                    self.assertFalse(paths.journal.exists())
                    if transition in {
                        "candidate_validated",
                        "prepared",
                        "backup_preserved",
                    }:
                        self.assertEqual(_bundle_label(target), "old")
                        self.assertTrue(paths.candidate.exists())
                    else:
                        self.assertEqual(_bundle_label(target), "new")
                        self.assertFalse(paths.candidate.exists())
                    self.assertFalse(paths.backup.exists())

    def test_two_concurrent_promoters_do_not_interleave_or_delete_target(self) -> None:
        if not precompute_sweep.HAVE_FLOCK:
            self.skipTest("fcntl.flock is unavailable on this platform")
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_promotion_concurrent_") as tmp:
            root = Path(tmp)
            target = root / "10x10"
            _write_competitor_bundle(target, "old")
            tx_ids = ("tx-concurrent-a", "tx-concurrent-b")
            labels = ("new-a", "new-b")
            for tx_id, label in zip(tx_ids, labels):
                paths = precompute_sweep._bundle_promotion_paths(target, tx_id)
                _write_competitor_bundle(paths.candidate, label)

            context = _safe_multiprocessing_context()
            queue: "mp.Queue[tuple[str, str]]" = context.Queue()
            processes = [
                context.Process(
                    target=_promote_in_process,
                    args=(
                        str(precompute_sweep._bundle_promotion_paths(target, tx_id).candidate),
                        str(target),
                        tx_id,
                        queue,
                    ),
                )
                for tx_id in tx_ids
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)

            results = [queue.get(timeout=5) for _ in processes]
            self.assertEqual(sorted(status for status, _ in results), ["ok", "ok"])
            self.assertTrue(precompute_sweep._bundle_dir_is_valid(target))
            self.assertIn(_bundle_label(target), set(labels))
            self.assertFalse(precompute_sweep._bundle_promotion_paths(target).journal.exists())
            for tx_id in tx_ids:
                paths = precompute_sweep._bundle_promotion_paths(target, tx_id)
                self.assertFalse(paths.candidate.exists())
                self.assertFalse(paths.backup.exists())


if __name__ == "__main__":
    unittest.main()
