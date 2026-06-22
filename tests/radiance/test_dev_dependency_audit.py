from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scripts.dev.dependency_audit import pip_audit_ignore_args


def test_pip_audit_ignore_args_uses_unexpired_allowlist_entry(tmp_path: Path) -> None:
    allowlist = tmp_path / "pip-audit-allowlist.toml"
    allowlist.write_text(
        """
[[vulnerability]]
id = "PYSEC-2026-1"
package = "example"
reason = "Accepted until the patched package is compatible."
expires = "2026-12-31"
owner = "Austin Rouse"
""".lstrip(),
        encoding="utf-8",
    )

    assert pip_audit_ignore_args(allowlist, today=date(2026, 6, 19)) == [
        "--ignore-vuln",
        "PYSEC-2026-1",
    ]


def test_pip_audit_ignore_args_rejects_expired_allowlist_entry(tmp_path: Path) -> None:
    allowlist = tmp_path / "pip-audit-allowlist.toml"
    allowlist.write_text(
        """
[[vulnerability]]
id = "PYSEC-2025-1"
package = "example"
reason = "Temporary exception."
expires = "2025-12-31"
owner = "Austin Rouse"
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expired on 2025-12-31"):
        pip_audit_ignore_args(allowlist, today=date(2026, 6, 19))
