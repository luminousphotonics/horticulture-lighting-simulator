from __future__ import annotations

import json
from dataclasses import dataclass
from typing import IO, Any, Mapping


@dataclass(frozen=True)
class ValidationResult:
    """Machine-readable validator outcome with JSON-safe scientific metrics."""

    validator: str
    passed: bool
    metrics: Mapping[str, Any]
    checks: Mapping[str, bool]

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "validator": self.validator,
            "passed": self.passed,
            "metrics": dict(self.metrics),
            "checks": dict(self.checks),
        }


def emit_validation_result(result: ValidationResult, *, stream: IO[str]) -> None:
    json.dump(result.to_payload(), stream, indent=2, sort_keys=True)
    stream.write("\n")
