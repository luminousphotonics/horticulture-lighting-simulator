"""Run the deterministic local baseline gates."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - dev gate automation invokes fixed command lists.
import sys
import tempfile
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_parser_module = importlib.import_module(
    "scripts.dev.finding_parsers" if __package__ else "finding_parsers"
)
_gate_module = importlib.import_module(
    "scripts.dev.fingerprint_gates" if __package__ else "fingerprint_gates"
)
parse_mypy_findings = _parser_module.parse_mypy_findings
parse_pytest_warnings = _parser_module.parse_pytest_warnings
parse_ruff_findings = _parser_module.parse_ruff_findings
parse_shellcheck_findings = _parser_module.parse_shellcheck_findings
compare_fingerprints = _gate_module.compare_fingerprints
fingerprint_set = _gate_module.fingerprint_set


ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
RATCHET_PATH = ROOT / "audit" / "debt_ratchet.json"
JS_ROOT = ROOT / "src" / "rad_rebuild" / "web" / "static" / "js"
HTML_ROOT = ROOT / "src" / "rad_rebuild" / "web" / "templates"
SOURCE_PATHS = ["app.py", "src", "tests", "scripts"]


@dataclass
class GateResult:
    name: str
    status: str
    command: list[str]
    exit_code: int | None
    details: dict[str, Any]


def env() -> dict[str, str]:
    values = os.environ.copy()
    existing = values.get("PYTHONPATH")
    values["PYTHONPATH"] = f"src{os.pathsep}{existing}" if existing else "src"
    values.setdefault("HYPOTHESIS_PROFILE", "phase03")
    values.setdefault("PYTHONHASHSEED", "0")
    return values


def run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        cwd=ROOT,
        env=env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def parse_json_payload(output: str) -> Any:
    stripped = output.strip()
    if not stripped:
        raise json.JSONDecodeError("empty output", output, 0)
    indexes = [
        index for index in (stripped.find("{"), stripped.find("[")) if index >= 0
    ]
    if indexes:
        return json.loads(stripped[min(indexes) :])
    raise json.JSONDecodeError("missing json payload", output, 0)


def load_ratchet() -> dict[str, Any]:
    return json.loads(RATCHET_PATH.read_text(encoding="utf-8"))


def tool_exists(command: str) -> bool:
    if "/" in command:
        return (ROOT / command).exists()
    return shutil.which(command) is not None


def gate_compileall() -> GateResult:
    command = [PYTHON, "-m", "compileall", "-q", *SOURCE_PATHS]
    result = run(command)
    return GateResult(
        "python_compileall",
        "pass" if result.returncode == 0 else "fail",
        command,
        result.returncode,
        {"stderr": result.stderr.strip()},
    )


def _pytest_test_count(output: str) -> int | None:
    matches = re.findall(r"(\d+)\s+passed", output)
    if matches:
        return int(matches[-1])
    match = re.search(r"collected\s+(\d+)\s+items?", output)
    return int(match.group(1)) if match else None


def _critical_coverage_files(coverage_json: Path) -> dict[str, int]:
    data = json.loads(coverage_json.read_text(encoding="utf-8"))
    files = data.get("files", {})
    critical = [
        "src/rad_rebuild/radiance/backend/models.py",
        "src/rad_rebuild/radiance/backend/runtime.py",
        "src/rad_rebuild/radiance/backend/workspace.py",
        "src/rad_rebuild/radiance/engine/layout/layout_generator.py",
        "src/rad_rebuild/radiance/engine/photometry/ppfd_metrics.py",
        "src/rad_rebuild/radiance/engine/simulation/precomputed_dataset.py",
    ]
    out: dict[str, int] = {}
    for path in critical:
        summary = files.get(path, {}).get("summary", {})
        if summary:
            out[path] = round(float(summary.get("percent_covered_display", 0)))
    return out


def gate_unit_coverage(ratchet: dict[str, Any]) -> GateResult:
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_coverage_") as tmp_dir:
        data_file = str(Path(tmp_dir) / ".coverage")
        json_file = Path(tmp_dir) / "coverage.json"
        command = [
            PYTHON,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--data-file",
            data_file,
            "-m",
            "pytest",
            "-q",
            "tests",
        ]
        result = run(command, timeout=240)
        combined = f"{result.stdout}\n{result.stderr}"
        tests = _pytest_test_count(combined)
        warning_findings = [
            asdict_finding
            for asdict_finding in (
                finding.__dict__ for finding in parse_pytest_warnings(combined)
            )
        ]
        first_party_warning_fingerprints = {
            str(finding["fingerprint"])
            for finding in warning_findings
            if finding.get("source_classification") == "first-party"
        }
        status = "pass"
        failures: list[str] = []
        if result.returncode != 0:
            if "No module named pytest" in combined:
                status = "missing"
                failures.append("pytest missing")
            else:
                status = "fail"
                failures.append("pytest tests failed")
        branch_percent = None
        critical_files: dict[str, int] = {}
        if result.returncode == 0:
            json_command = [
                PYTHON,
                "-m",
                "coverage",
                "json",
                "--data-file",
                data_file,
                "-o",
                str(json_file),
                "--quiet",
            ]
            json_result = run(json_command)
            if json_result.returncode == 0 and json_file.exists():
                totals = json.loads(json_file.read_text(encoding="utf-8"))["totals"]
                branch_percent = round(float(totals.get("percent_covered_display", 0)))
                critical_files = _critical_coverage_files(json_file)
                minimum = ratchet["coverage"]["min_branch_percent"]
                if branch_percent < minimum:
                    status = "fail"
                    failures.append(
                        f"coverage {branch_percent}% below ratchet {minimum}%"
                    )
            else:
                status = "fail"
                failures.append("coverage json failed")
        warning_ratchet = ratchet.get("pytest_warnings", {})
        if "allowed_first_party_fingerprints" in warning_ratchet:
            allowed = set(warning_ratchet["allowed_first_party_fingerprints"])
            warning_comparison = compare_fingerprints(
                first_party_warning_fingerprints, allowed
            )
            if warning_comparison["new"]:
                status = "fail"
                failures.append(
                    f"{len(warning_comparison['new'])} new first-party warnings"
                )
        else:
            warning_comparison = {"new": [], "resolved": []}

    return GateResult(
        "pytest_coverage",
        status,
        command,
        result.returncode,
        {
            "tests": tests,
            "branch_percent": branch_percent,
            "critical_files": critical_files,
            "first_party_warning_fingerprints": sorted(
                first_party_warning_fingerprints
            ),
            "warning_fingerprints": sorted(
                str(finding["fingerprint"]) for finding in warning_findings
            ),
            "warning_regression": warning_comparison,
            "failures": failures,
        },
    )


def gate_ruff(ratchet: dict[str, Any]) -> GateResult:
    command = [PYTHON, "-m", "ruff", "check", ".", "--output-format", "json"]
    result = run(command)
    if result.returncode == 1:
        findings = [finding.__dict__ for finding in parse_ruff_findings(result.stdout)]
    elif result.returncode == 0:
        findings = []
    else:
        return GateResult(
            "ruff",
            "fail",
            command,
            result.returncode,
            {"stderr": result.stderr.strip()},
        )
    current = fingerprint_set(findings, tool="ruff")
    if "allowed_fingerprints" in ratchet["ruff"]:
        allowed = set(ratchet["ruff"]["allowed_fingerprints"])
        comparison = compare_fingerprints(current, allowed)
        status = "pass" if not comparison["new"] else "fail"
        details = {
            "findings": len(current),
            "allowed": len(allowed),
            "new": comparison["new"],
            "resolved": comparison["resolved"],
        }
    else:
        maximum = ratchet["ruff"]["max_findings"]
        status = "pass" if len(current) <= maximum else "fail"
        details = {"findings": len(current), "max": maximum}
    return GateResult("ruff", status, command, result.returncode, details)


def gate_mypy(ratchet: dict[str, Any]) -> GateResult:
    command = [
        PYTHON,
        "-m",
        "mypy",
        "--show-error-codes",
        "app.py",
        "src",
        "tests",
        "scripts",
    ]
    result = run(command)
    findings = [finding.__dict__ for finding in parse_mypy_findings(result.stdout)]
    current = fingerprint_set(findings, tool="mypy")
    if result.returncode not in {0, 1}:
        return GateResult(
            "mypy",
            "fail",
            command,
            result.returncode,
            {"stderr": result.stderr.strip()},
        )
    if "allowed_fingerprints" in ratchet["mypy"]:
        allowed = set(ratchet["mypy"]["allowed_fingerprints"])
        comparison = compare_fingerprints(current, allowed)
        status = "pass" if not comparison["new"] else "fail"
        details = {
            "errors": len(current),
            "allowed": len(allowed),
            "new": comparison["new"],
            "resolved": comparison["resolved"],
        }
    else:
        maximum = ratchet["mypy"]["max_errors"]
        status = "pass" if len(current) <= maximum else "fail"
        details = {"errors": len(current), "max": maximum}
    return GateResult("mypy", status, command, result.returncode, details)


def gate_bandit(ratchet: dict[str, Any]) -> GateResult:
    command = [PYTHON, "-m", "bandit", "-r", "app.py", "src", "scripts", "-f", "json"]
    result = run(command)
    high: int | None
    medium: int | None
    low: int | None
    if result.returncode in {0, 1} and result.stdout.strip():
        data = parse_json_payload(result.stdout)
        metrics = data.get("metrics", {}).get("_totals", {})
        high = int(metrics.get("SEVERITY.HIGH", 0))
        medium = int(metrics.get("SEVERITY.MEDIUM", 0))
        low = int(metrics.get("SEVERITY.LOW", 0))
        status = "pass"
        limits = ratchet["bandit"]
        if (
            high > limits["max_high"]
            or medium > limits["max_medium"]
            or low > limits["max_low"]
        ):
            status = "fail"
    else:
        high = None
        medium = None
        low = None
        limits = ratchet["bandit"]
        status = "fail"
    return GateResult(
        "bandit",
        status,
        command,
        result.returncode,
        {"high": high, "medium": medium, "low": low, "max": limits},
    )


def shell_files() -> list[str]:
    return sorted(str(path.relative_to(ROOT)) for path in ROOT.glob("scripts/**/*.sh"))


def gate_shell_parse() -> GateResult:
    failures: list[str] = []
    commands: list[list[str]] = []
    for path in shell_files():
        command = ["bash", "-n", path]
        commands.append(command)
        result = run(command)
        if result.returncode != 0:
            failures.append(path)
    return GateResult(
        "bash_parse",
        "pass" if not failures else "fail",
        ["bash", "-n", "scripts/**/*.sh"],
        0 if not failures else 1,
        {"files": len(commands), "failures": failures},
    )


def gate_shellcheck(ratchet: dict[str, Any]) -> GateResult:
    if not tool_exists("shellcheck"):
        return GateResult("shellcheck", "missing", ["shellcheck"], None, {})
    files = shell_files()
    command = ["shellcheck", "-f", "json", *files]
    result = run(command)
    findings = [
        finding.__dict__ for finding in parse_shellcheck_findings(result.stdout or "[]")
    ]
    current = fingerprint_set(findings, tool="shellcheck")
    if "allowed_fingerprints" in ratchet["shellcheck"]:
        allowed = set(ratchet["shellcheck"]["allowed_fingerprints"])
        comparison = compare_fingerprints(current, allowed)
        status = (
            "pass" if result.returncode in {0, 1} and not comparison["new"] else "fail"
        )
        details = {
            "findings": len(current),
            "allowed": len(allowed),
            "new": comparison["new"],
            "resolved": comparison["resolved"],
        }
    else:
        maximum = ratchet["shellcheck"]["max_findings"]
        status = (
            "pass"
            if result.returncode in {0, 1} and len(current) <= maximum
            else "fail"
        )
        details = {"findings": len(current), "max": maximum}
    return GateResult("shellcheck", status, command, result.returncode, details)


def js_files() -> list[str]:
    return sorted(str(path.relative_to(ROOT)) for path in JS_ROOT.rglob("*.js"))


def gate_node_parse() -> GateResult:
    if not tool_exists("node"):
        return GateResult("node_parse", "missing", ["node"], None, {})
    failures: list[str] = []
    for path in js_files():
        result = run(["node", "--check", path])
        if result.returncode != 0:
            failures.append(path)
    return GateResult(
        "node_parse",
        "pass" if not failures else "fail",
        ["node", "--check", "src/rad_rebuild/web/static/js/**/*.js"],
        0 if not failures else 1,
        {"files": len(js_files()), "failures": failures},
    )


def gate_frontend_types() -> GateResult:
    command = [PYTHON, "scripts/dev/generate_frontend_types.py", "--check"]
    result = run(command)
    return GateResult(
        "frontend_api_types",
        "pass" if result.returncode == 0 else "fail",
        command,
        result.returncode,
        {"stdout": result.stdout.strip(), "stderr": result.stderr.strip()},
    )


def gate_tsc() -> GateResult:
    tsc = "./node_modules/.bin/tsc"
    if not tool_exists(tsc):
        return GateResult("typescript_check", "missing", [tsc], None, {})
    command = [tsc, "--noEmit"]
    result = run(command)
    return GateResult(
        "typescript_check",
        "pass" if result.returncode == 0 else "fail",
        command,
        result.returncode,
        {"stdout": result.stdout.strip(), "stderr": result.stderr.strip()},
    )


def gate_eslint(ratchet: dict[str, Any]) -> GateResult:
    eslint = "./node_modules/.bin/eslint"
    if not tool_exists(eslint):
        return GateResult("eslint", "missing", [eslint], None, {})
    command = [
        eslint,
        "--no-config-lookup",
        "--format",
        "json",
        "--rule",
        "no-undef:error",
        "--rule",
        'no-unused-vars:["warn",{"argsIgnorePattern":"^_","caughtErrorsIgnorePattern":"^_"}]',
        "--rule",
        "no-redeclare:error",
        "--global",
        "window",
        "--global",
        "document",
        "--global",
        "fetch",
        "--global",
        "FormData",
        "--global",
        "URLSearchParams",
        "--global",
        "URL",
        "--global",
        "setTimeout",
        "--global",
        "setInterval",
        "--global",
        "clearInterval",
        "--global",
        "navigator",
        "--global",
        "HTMLElement",
        "--global",
        "HTMLButtonElement",
        "--global",
        "Node",
        "--global",
        "DOMParser",
        *js_files(),
    ]
    result = run(command)
    data = parse_json_payload(result.stdout or "[]")
    errors = sum(int(item.get("errorCount", 0)) for item in data)
    warnings = sum(int(item.get("warningCount", 0)) for item in data)
    limits = ratchet["eslint"]
    status = (
        "pass"
        if result.returncode in {0, 1}
        and errors <= limits["max_errors"]
        and warnings <= limits["max_warnings"]
        else "fail"
    )
    return GateResult(
        "eslint",
        status,
        command,
        result.returncode,
        {"errors": errors, "warnings": warnings, "max": limits},
    )


def gate_html_validate(ratchet: dict[str, Any]) -> GateResult:
    del ratchet
    html_validate = "./node_modules/.bin/html-validate"
    if not tool_exists(html_validate):
        return GateResult("html_validate", "missing", [html_validate], None, {})
    routes = [
        ("/", "root.html"),
        ("/radiance-simulator", "radiance_simulator.html"),
    ]
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_rendered_html_") as tmp_dir:
        output_dir = Path(tmp_dir)
        try:
            from rad_rebuild.web.app import app as flask_app

            client = flask_app.test_client()
            files: list[str] = []
            for route, filename in routes:
                response = client.get(route, headers={"Host": "localhost"})
                if response.status_code != 200:
                    return GateResult(
                        "html_validate",
                        "fail",
                        [html_validate, "--formatter", "json", "rendered Flask pages"],
                        response.status_code,
                        {"route": route, "failure": "render failed"},
                    )
                path = output_dir / filename
                path.write_text(response.get_data(as_text=True), encoding="utf-8")
                files.append(str(path))
        except Exception as exc:
            return GateResult(
                "html_validate",
                "fail",
                [html_validate, "--formatter", "json", "rendered Flask pages"],
                1,
                {"failure": f"render failed: {exc}"},
            )
        command = [html_validate, "--formatter", "json", *files]
        result = run(command)
    data = parse_json_payload(result.stdout or "[]")
    errors = sum(int(item.get("errorCount", 0)) for item in data)
    maximum = 0
    status = "pass" if result.returncode in {0, 1} and errors <= maximum else "fail"
    return GateResult(
        "html_validate",
        status,
        command,
        result.returncode,
        {"errors": errors, "max": maximum, "routes": [route for route, _filename in routes]},
    )


def main() -> int:
    ratchet = load_ratchet()
    gates = [
        gate_compileall(),
        gate_unit_coverage(ratchet),
        gate_ruff(ratchet),
        gate_mypy(ratchet),
        gate_bandit(ratchet),
        gate_shell_parse(),
        gate_shellcheck(ratchet),
        gate_node_parse(),
        gate_frontend_types(),
        gate_tsc(),
        gate_eslint(ratchet),
        gate_html_validate(ratchet),
    ]
    payload = {
        "policy": "fail on hard gate failures or ratchet increases; report missing optional tools separately",
        "results": [
            {
                "name": gate.name,
                "status": gate.status,
                "exit_code": gate.exit_code,
                "command": gate.command,
                "details": gate.details,
            }
            for gate in gates
        ],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if any(gate.status == "fail" for gate in gates) else 0


if __name__ == "__main__":
    raise SystemExit(main())
