from __future__ import annotations

import io
import importlib.util
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch


DOWNLOAD_PRECOMPUTED_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "radiance"
    / "download_precomputed.py"
)
SPEC = importlib.util.spec_from_file_location(
    "download_precomputed_under_test", DOWNLOAD_PRECOMPUTED_PATH
)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("Unable to load download_precomputed.py for tests.")
download_precomputed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = download_precomputed
SPEC.loader.exec_module(download_precomputed)


def _write_tar(path: Path, members: list[tarfile.TarInfo | tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            if isinstance(member, tuple):
                name, data = member
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                archive.addfile(member)


class DownloadPrecomputedSecurityTests(unittest.TestCase):
    def test_remote_archive_requires_checksum_before_download(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_download_checksum_"
        ) as tmp:
            with patch.object(
                download_precomputed,
                "download_or_copy",
                side_effect=AssertionError("download called"),
            ):
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    download_precomputed.install_archive(
                        "manual",
                        "https://example.com/precomputed.tar.gz",
                        "",
                        Path(tmp) / "dest",
                        False,
                        Path(tmp),
                    )

    def test_tar_symlink_member_is_rejected_and_leaves_no_partial_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_tar_symlink_") as tmp:
            root = Path(tmp)
            archive_path = root / "hostile.tar.gz"
            dest = root / "dest"
            link = tarfile.TarInfo("smd/10x10/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../outside"
            _write_tar(archive_path, [("smd/10x10/good.txt", b"good"), link])

            with self.assertRaisesRegex(RuntimeError, "link|symlink|unsafe"):
                download_precomputed.extract_archive(archive_path, dest, force=False)

            self.assertFalse((dest / "smd" / "10x10" / "good.txt").exists())
            self.assertFalse((dest / "smd" / "10x10" / "link").exists())

    def test_identical_existing_file_is_treated_as_installed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_tar_identical_") as tmp:
            root = Path(tmp)
            archive_path = root / "identical.tar.gz"
            dest = root / "dest"
            existing = dest / "smd" / "10x10" / "manifest.json"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b'{"bundle": "demo"}')
            _write_tar(
                archive_path,
                [
                    ("smd/10x10/manifest.json", b'{"bundle": "demo"}'),
                    ("smd/10x10/new.txt", b"new"),
                ],
            )

            download_precomputed.extract_archive(archive_path, dest, force=False)

            self.assertEqual(existing.read_bytes(), b'{"bundle": "demo"}')
            self.assertEqual(
                (dest / "smd" / "10x10" / "new.txt").read_bytes(), b"new"
            )

    def test_different_existing_file_requires_force(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_tar_different_") as tmp:
            root = Path(tmp)
            archive_path = root / "different.tar.gz"
            dest = root / "dest"
            existing = dest / "smd" / "10x10" / "manifest.json"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing")
            _write_tar(
                archive_path,
                [
                    ("smd/10x10/manifest.json", b"replacement"),
                    ("smd/10x10/new.txt", b"new"),
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                download_precomputed.extract_archive(archive_path, dest, force=False)

            self.assertEqual(existing.read_bytes(), b"existing")
            self.assertFalse((dest / "smd" / "10x10" / "new.txt").exists())

    def test_force_replaces_different_existing_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_tar_force_") as tmp:
            root = Path(tmp)
            archive_path = root / "force.tar.gz"
            dest = root / "dest"
            existing = dest / "smd" / "10x10" / "manifest.json"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing")
            _write_tar(archive_path, [("smd/10x10/manifest.json", b"replacement")])

            download_precomputed.extract_archive(archive_path, dest, force=True)

            self.assertEqual(existing.read_bytes(), b"replacement")

    def test_zip_duplicate_member_is_rejected_and_existing_file_is_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_zip_duplicate_") as tmp:
            root = Path(tmp)
            archive_path = root / "duplicate.zip"
            dest = root / "dest"
            existing = dest / "smd" / "10x10" / "manifest.json"
            existing.parent.mkdir(parents=True)
            existing.write_text("existing", encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("smd/10x10/new.txt", "first")
                    archive.writestr("smd/10x10/new.txt", "second")

            with self.assertRaisesRegex(RuntimeError, "duplicate|conflict"):
                download_precomputed.extract_archive(archive_path, dest, force=True)

            self.assertEqual(existing.read_text(encoding="utf-8"), "existing")
            self.assertFalse((dest / "smd" / "10x10" / "new.txt").exists())

    def test_zip_excessive_expanded_size_is_rejected_without_partial_install(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_zip_size_") as tmp:
            root = Path(tmp)
            archive_path = root / "large.zip"
            dest = root / "dest"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "smd/10x10/huge.bin",
                    b"x" * (download_precomputed.MAX_ARCHIVE_MEMBER_BYTES + 1),
                )

            with self.assertRaisesRegex(RuntimeError, "size|large"):
                download_precomputed.extract_archive(archive_path, dest, force=False)

            self.assertFalse((dest / "smd" / "10x10" / "huge.bin").exists())


if __name__ == "__main__":
    unittest.main()
