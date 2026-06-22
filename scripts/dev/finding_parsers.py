"""Small finding parsers used by the durable baseline gate."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

_gate_module = importlib.import_module(
    "scripts.dev.fingerprint_gates" if __package__ else "fingerprint_gates"
)
source_classification = _gate_module.source_classification


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class GateFinding:
    fingerprint: str
    tool: str
    code: str
    path: str
    line: int | None
    column: int | None
    symbol: str
    message: str
    source_classification: str


def stable_hash(parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def normalize_path(path: str) -> str:
    if not path:
        return "<unknown>"
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(ROOT))
    except (OSError, ValueError):
        return path


def _symbol_cache() -> dict[str, list[tuple[int, int, str]]]:
    cache: dict[str, list[tuple[int, int, str]]] = {}
    for path in [
        Path("app.py"),
        *Path("src").rglob("*.py"),
        *Path("tests").rglob("*.py"),
        *Path("scripts").rglob("*.py"),
    ]:
        full_path = ROOT / path
        try:
            tree = ast.parse(full_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        symbols: list[tuple[int, int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                end_line = int(getattr(node, "end_lineno", node.lineno))
                symbols.append((node.lineno, end_line, node.name))
        cache[str(path)] = sorted(symbols, key=lambda item: item[0])
    return cache


SYMBOL_CACHE = _symbol_cache()


def symbol_for(path: str, line: int | None) -> str:
    if line is None:
        return ""
    for start, end, symbol in SYMBOL_CACHE.get(path, []):
        if start <= line <= end:
            return symbol
    return ""


def finding_fingerprint(
    tool: str,
    code: str,
    path: str,
    line: int | None,
    column: int | None,
    symbol: str,
    message: str,
) -> str:
    payload: dict[str, object] = {
        "tool": tool,
        "code": code,
        "path": path,
        "symbol": symbol,
        "message": re.sub(r"\s+", " ", message).strip(),
    }
    if tool != "mypy":
        payload["line"] = line or ""
        payload["column"] = column or ""
    return stable_hash(payload)


def build_finding(
    *,
    tool: str,
    code: str,
    path: str,
    line: int | None,
    column: int | None,
    message: str,
    symbol: str | None = None,
) -> GateFinding:
    normalized = normalize_path(path)
    resolved_symbol = symbol if symbol is not None else symbol_for(normalized, line)
    return GateFinding(
        fingerprint=finding_fingerprint(
            tool, code, normalized, line, column, resolved_symbol, message
        ),
        tool=tool,
        code=code,
        path=normalized,
        line=line,
        column=column,
        symbol=resolved_symbol,
        message=message,
        source_classification=source_classification(normalized),
    )


def parse_ruff_findings(stdout: str) -> list[GateFinding]:
    entries = json.loads(stdout or "[]")
    findings: list[GateFinding] = []
    for entry in entries:
        location = entry.get("location") or {}
        findings.append(
            build_finding(
                tool="ruff",
                code=str(entry.get("code") or "unknown"),
                path=str(entry.get("filename") or ""),
                line=int(location.get("row") or 0) or None,
                column=int(location.get("column") or 0) or None,
                message=str(entry.get("message") or ""),
            )
        )
    return findings


MYPY_RE = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+)(?::(?P<column>\d+))?: error: (?P<message>.*?)(?:\s+\[(?P<code>[^\]]+)\])?$"
)


def parse_mypy_findings(stdout: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for line in stdout.splitlines():
        match = MYPY_RE.match(line)
        if not match:
            continue
        findings.append(
            build_finding(
                tool="mypy",
                code=match.group("code") or "unknown",
                path=match.group("path"),
                line=int(match.group("line")),
                column=int(match.group("column")) if match.group("column") else None,
                message=match.group("message"),
            )
        )
    return findings


def parse_shellcheck_findings(stdout: str) -> list[GateFinding]:
    entries = json.loads(stdout or "[]")
    findings: list[GateFinding] = []
    for entry in entries:
        findings.append(
            build_finding(
                tool="shellcheck",
                code=f"SC{entry.get('code')}",
                path=str(entry.get("file") or ""),
                line=int(entry.get("line") or 0) or None,
                column=int(entry.get("column") or 0) or None,
                message=str(entry.get("message") or ""),
            )
        )
    return findings


WARNING_RE = re.compile(
    r"^(?P<path>[^:\n]+):(?P<line>\d+): (?P<category>[A-Za-z_][\w.]*Warning): (?P<message>.*)$"
)


def parse_pytest_warnings(output: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for line in output.splitlines():
        match = WARNING_RE.match(line.strip())
        if not match:
            continue
        findings.append(
            build_finding(
                tool="pytest_warning",
                code=match.group("category"),
                path=match.group("path"),
                line=int(match.group("line")),
                column=None,
                message=match.group("message"),
            )
        )
    return findings
