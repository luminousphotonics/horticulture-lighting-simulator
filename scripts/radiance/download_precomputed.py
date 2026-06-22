#!/usr/bin/env python3
"""Download and install public precomputed Radiance datasets."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DESTINATION = REPO_ROOT / "data" / "radiance" / "precomputed"

# Fill these in after GitHub Release assets exist. Leave blank until then.
FULL_DATASET_ARCHIVE_URLS = {
    "smd": "https://github.com/luminousphotonics/horticulture-lighting-simulator-bundles/releases/download/precomputed-v1/precomputed-smd-10x10-30x30.tar.gz",
    "competitor_practical": "https://github.com/luminousphotonics/horticulture-lighting-simulator-bundles/releases/download/precomputed-v1/precomputed-competitor-practical-10x10-30x30.tar.gz",
    "hps_karma_4x4": "https://github.com/luminousphotonics/horticulture-lighting-simulator-bundles/releases/download/precomputed-v1/precomputed-hps-karma-4x4-10x10-30x30.tar.gz",
}

# Optional future integrity checks, keyed the same way as FULL_DATASET_ARCHIVE_URLS.
FULL_DATASET_SHA256 = {
    "smd": "342d9c15fa4e4e54721efc34b49b9b1124097bd4236bab04cfc062e7d1d359c0",
    "competitor_practical": "4cf28481c092ac6ff38253973fdb38fb84e60cfe23f2504b190bb99ec64f8e16",
    "hps_karma_4x4": "5a9db2751aed2fa63b09def8438addfffd8d97f3ad36c4037aaacec82166dd6f",
}

EXPECTED_ARCHIVE_NAMES = {
    "smd": "precomputed-smd-10x10-30x30.tar.gz",
    "competitor_practical": "precomputed-competitor-practical-10x10-30x30.tar.gz",
    "hps_karma_4x4": "precomputed-hps-karma-4x4-10x10-30x30.tar.gz",
}

DOWNLOAD_TIMEOUT_S = 30
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: int
    is_dir: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and extract Radiance precomputed datasets.")
    parser.add_argument("--dataset", choices=["full"], default="full", help="Dataset bundle to install.")
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Archive URL or local archive path to install. May be passed more than once.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"Destination precomputed root. Default: {DEFAULT_DESTINATION}",
    )
    parser.add_argument("--force", action="store_true", help="Allow archive contents to overwrite existing files.")
    return parser.parse_args()


def configured_full_urls() -> list[tuple[str, str, str]]:
    configured: list[tuple[str, str, str]] = []
    for key, url in FULL_DATASET_ARCHIVE_URLS.items():
        clean = str(url or "").strip()
        if clean:
            configured.append((key, clean, str(FULL_DATASET_SHA256.get(key, "") or "").strip()))
    return configured


def print_missing_url_instructions() -> None:
    print("Full precomputed release URLs are not configured yet.")
    print("After GitHub Release assets exist, set FULL_DATASET_ARCHIVE_URLS near the top of this script.")
    print("Planned archives:")
    for name in EXPECTED_ARCHIVE_NAMES.values():
        print(f"  - {name}")
    print("")
    print("For manual testing, pass one or more archives directly:")
    print("  python scripts/radiance/download_precomputed.py --dataset full --url /path/to/archive.tar.gz")
    print("  python scripts/radiance/download_precomputed.py --dataset full --url https://example.com/archive.tar.gz")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_remote_source(source: str) -> bool:
    return urlparse(source).scheme in {"http", "https"}


def download_or_copy(source: str, work_dir: Path, checksum: str = "") -> Path:
    parsed = urlparse(source)
    filename = Path(parsed.path).name if parsed.scheme else Path(source).name
    if not filename:
        filename = "precomputed-archive"
    target = work_dir / filename
    if parsed.scheme:
        if parsed.scheme != "https":
            raise RuntimeError("Remote precomputed archives must use HTTPS.")
        if not checksum:
            raise RuntimeError("Remote precomputed archives require a SHA-256 checksum.")
        print(f"Downloading {source}")
        digest = hashlib.sha256()
        total = 0
        request = urllib.request.Request(source, headers={"User-Agent": "rad-rebuild-precomputed-downloader/1"})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response, target.open("wb") as out_handle:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise RuntimeError("Remote archive exceeds the maximum allowed download size.")
                digest.update(chunk)
                out_handle.write(chunk)
        actual = digest.hexdigest()
        if actual.lower() != checksum.lower():
            target.unlink(missing_ok=True)
            raise RuntimeError(f"Checksum mismatch for remote archive: expected {checksum}, got {actual}")
        return target

    local = Path(source).expanduser()
    if not local.exists():
        raise FileNotFoundError(f"Archive not found: {source}")
    print(f"Using local archive {local}")
    shutil.copyfile(local, target)
    return target


def _safe_member_parts(member_name: str) -> tuple[str, ...]:
    clean = member_name.strip()
    if not clean:
        raise RuntimeError("Refusing empty archive member name.")
    if "\\" in clean:
        raise RuntimeError(f"Refusing unsafe archive member: {member_name}")
    member_path = Path(clean)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise RuntimeError(f"Refusing unsafe archive member: {member_name}")
    return tuple(part for part in member_path.parts if part not in {"", "."})


def safe_target(dest: Path, member_name: str) -> Path:
    parts = _safe_member_parts(member_name)
    target = (dest.joinpath(*parts)).resolve()
    dest_resolved = dest.resolve()
    if target != dest_resolved and dest_resolved not in target.parents:
        raise RuntimeError(f"Refusing archive member outside destination: {member_name}")
    return target


def tar_members(path: Path) -> list[tarfile.TarInfo]:
    with tarfile.open(path, "r:*") as archive:
        return archive.getmembers()


def zip_members(path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(path) as archive:
        return archive.infolist()


def check_overwrite(dest: Path, relative_names: list[str], force: bool) -> None:
    if force:
        return
    conflicts = []
    for name in relative_names:
        if not name or name.endswith("/"):
            continue
        target = safe_target(dest, name)
        if target.exists():
            conflicts.append(target)
            if len(conflicts) >= 8:
                break
    if conflicts:
        rendered = "\n".join(f"  - {path}" for path in conflicts)
        raise RuntimeError(f"Archive would overwrite existing files. Re-run with --force to replace them:\n{rendered}")


def _validate_member_set(members: list[ArchiveMember]) -> list[ArchiveMember]:
    if len(members) > MAX_ARCHIVE_FILES:
        raise RuntimeError("Archive contains too many files.")
    seen: dict[tuple[str, ...], bool] = {}
    total_size = 0
    validated: list[ArchiveMember] = []
    for member in members:
        parts = _safe_member_parts(member.name)
        if not parts:
            continue
        if parts in seen:
            raise RuntimeError(f"Archive contains duplicate path: {member.name}")
        for index in range(1, len(parts)):
            if seen.get(parts[:index]) is False:
                raise RuntimeError(f"Archive contains conflicting path: {member.name}")
        if member.is_dir:
            for existing, is_dir in seen.items():
                if not is_dir and len(existing) > len(parts) and existing[: len(parts)] == parts:
                    raise RuntimeError(f"Archive contains conflicting path: {member.name}")
        seen[parts] = member.is_dir
        if member.size < 0:
            raise RuntimeError(f"Archive member has invalid size: {member.name}")
        if member.size > MAX_ARCHIVE_MEMBER_BYTES:
            raise RuntimeError(f"Archive member is too large: {member.name}")
        total_size += member.size
        if total_size > MAX_ARCHIVE_TOTAL_BYTES:
            raise RuntimeError("Archive expanded size is too large.")
        validated.append(member)
    return validated


def _tar_archive_members(path: Path) -> list[ArchiveMember]:
    members: list[ArchiveMember] = []
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            if member.isdir():
                members.append(ArchiveMember(member.name, 0, True))
                continue
            if not member.isfile():
                raise RuntimeError(f"Refusing unsafe archive link or special file: {member.name}")
            if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise RuntimeError(f"Refusing unsafe archive permissions: {member.name}")
            members.append(ArchiveMember(member.name, int(member.size), False))
    return _validate_member_set(members)


def _zip_archive_members(path: Path) -> list[ArchiveMember]:
    members: list[ArchiveMember] = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}:
                raise RuntimeError(f"Refusing unsafe archive link or special file: {member.filename}")
            if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise RuntimeError(f"Refusing unsafe archive permissions: {member.filename}")
            members.append(ArchiveMember(member.filename, int(member.file_size), member.is_dir()))
    return _validate_member_set(members)


def _copy_stream(src, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out_handle:
        shutil.copyfileobj(src, out_handle, length=DOWNLOAD_CHUNK_BYTES)
        out_handle.flush()
        os.fsync(out_handle.fileno())
    os.chmod(dest, 0o644)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _extract_tar_to_stage(path: Path, stage: Path, members: list[ArchiveMember]) -> None:
    allowed = {member.name: member for member in members}
    with tarfile.open(path, "r:*") as archive:
        for member in archive.getmembers():
            spec = allowed.get(member.name)
            if spec is None:
                continue
            target = safe_target(stage, spec.name)
            if spec.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o700)
                continue
            src = archive.extractfile(member)
            if src is None:
                raise RuntimeError(f"Unable to read archive member: {member.name}")
            with src:
                _copy_stream(src, target)


def _extract_zip_to_stage(path: Path, stage: Path, members: list[ArchiveMember]) -> None:
    allowed = {member.name: member for member in members}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            spec = allowed.get(member.filename)
            if spec is None:
                continue
            target = safe_target(stage, spec.name)
            if spec.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target, 0o700)
                continue
            with archive.open(member) as src:
                _copy_stream(src, target)


def _relative_stage_files(stage: Path) -> list[Path]:
    return sorted(path.relative_to(stage) for path in stage.rglob("*") if path.is_file())


def _install_staged_files(stage: Path, dest: Path, force: bool) -> None:
    files = _relative_stage_files(stage)
    check_overwrite(dest, [str(path) for path in files], force)
    installed: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    backup_root = Path(tempfile.mkdtemp(prefix=".rad-precomputed-backup-", dir=str(dest.parent)))
    try:
        dest.mkdir(parents=True, exist_ok=True)
        for rel_path in files:
            source = stage / rel_path
            target = safe_target(dest, str(rel_path))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup = backup_root / rel_path
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
                backups.append((target, backup))
            os.replace(source, target)
            installed.append(target)
            _fsync_dir(target.parent)
        _fsync_dir(dest)
    except Exception:
        for path in reversed(installed):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        for target, backup in reversed(backups):
            target.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        shutil.rmtree(backup_root, ignore_errors=True)


def extract_archive(path: Path, dest: Path, force: bool) -> None:
    if tarfile.is_tarfile(path):
        members = _tar_archive_members(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rad-precomputed-stage-", dir=str(dest.parent)) as tmp:
            stage = Path(tmp)
            _extract_tar_to_stage(path, stage, members)
            _install_staged_files(stage, dest, force)
        return

    if zipfile.is_zipfile(path):
        members = _zip_archive_members(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rad-precomputed-stage-", dir=str(dest.parent)) as tmp:
            stage = Path(tmp)
            _extract_zip_to_stage(path, stage, members)
            _install_staged_files(stage, dest, force)
        return

    raise RuntimeError(f"Unsupported archive format: {path}")


def install_archive(key: str, source: str, checksum: str, dest: Path, force: bool, work_dir: Path) -> None:
    if _is_remote_source(source) and not checksum:
        raise RuntimeError(f"Remote archive {key} requires a SHA-256 checksum.")
    archive_path = download_or_copy(source, work_dir, checksum)
    if checksum and not _is_remote_source(source):
        actual = sha256_file(archive_path)
        if actual.lower() != checksum.lower():
            raise RuntimeError(f"Checksum mismatch for {key}: expected {checksum}, got {actual}")
    print(f"Extracting {archive_path.name} into {dest}")
    extract_archive(archive_path, dest, force)


def installed_summary(dest: Path) -> list[str]:
    if not dest.exists():
        return []
    rows: list[str] = []
    for mode_dir in sorted(path for path in dest.iterdir() if path.is_dir()):
        bundles = sorted(path.name for path in mode_dir.iterdir() if path.is_dir())
        if bundles:
            rows.append(f"{mode_dir.name}: {len(bundles)} bundles ({bundles[0]}..{bundles[-1]})")
    return rows


def main() -> int:
    args = parse_args()
    dest = args.dest.expanduser().resolve()
    sources = [("manual", url, "") for url in args.url]
    if not sources:
        sources = configured_full_urls()
    if not sources:
        print_missing_url_instructions()
        return 2

    with tempfile.TemporaryDirectory(prefix="rad-precomputed-download-") as tmp:
        work_dir = Path(tmp)
        for key, source, checksum in sources:
            install_archive(key, source, checksum, dest, args.force, work_dir)

    print("")
    print(f"Installed precomputed dataset files under: {dest}")
    summary = installed_summary(dest)
    if summary:
        print("Installed dataset summary:")
        for row in summary:
            print(f"  - {row}")
    else:
        print("No bundle directories were found after extraction; check archive structure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
