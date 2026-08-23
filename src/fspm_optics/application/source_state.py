"""Immutable linkage from the baseline stage to later scientific stages."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

PHYSICAL_SOURCE_STATE_SCHEMA_ID = "fspm-optics.physical-source-state"
PHYSICAL_SOURCE_STATE_SCHEMA_VERSION = 1


class PhysicalSourceStateError(ValueError):
    """A Stage A source-state payload is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class PhysicalSourceState:
    """Canonical, path-free final source operation selected by Stage A.

    The canonical JSON string is retained instead of mutable nested mappings.
    Consumers receive a fresh decoded value, so a later stage cannot mutate the
    authoritative handoff in place.
    """

    canonical_payload_json: str
    source_state_id: str

    @classmethod
    def create(
        cls,
        *,
        system_id: str,
        layout_identity: Mapping[str, object],
        full_output_schedule: Mapping[str, object],
        operating_point: Mapping[str, object],
        source_operation: Mapping[str, object],
    ) -> "PhysicalSourceState":
        if not all(
            isinstance(value, Mapping) and value
            for value in (
                layout_identity,
                full_output_schedule,
                operating_point,
                source_operation,
            )
        ):
            raise PhysicalSourceStateError(
                "physical source state requires non-empty authoritative inputs."
            )
        payload: dict[str, object] = {
            "schema_id": PHYSICAL_SOURCE_STATE_SCHEMA_ID,
            "schema_version": PHYSICAL_SOURCE_STATE_SCHEMA_VERSION,
            "system_id": str(system_id),
            "stage_role": "baseline_ppfd",
            "plants_in_scientific_transport": False,
            "layout_identity_sha256": _hash_json(layout_identity),
            "full_output_schedule_sha256": _hash_json(full_output_schedule),
            "operating_point_sha256": _hash_json(operating_point),
            "source_operation": dict(source_operation),
            "handoff_contract": {
                "target_control_recomputed_by_later_stage": False,
                "fspm_reference_controls_transport": False,
                "post_trace_reconciliation": False,
            },
        }
        canonical = _canonical_json(payload)
        source_state_id = "physical-source-state-v1-" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        return cls(canonical, source_state_id)

    def __post_init__(self) -> None:
        try:
            payload = json.loads(self.canonical_payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PhysicalSourceStateError(
                "physical source state canonical JSON is invalid."
            ) from exc
        if not isinstance(payload, dict):
            raise PhysicalSourceStateError(
                "physical source state payload must be an object."
            )
        canonical = _canonical_json(payload)
        expected_id = "physical-source-state-v1-" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        expected_handoff = {
            "target_control_recomputed_by_later_stage": False,
            "fspm_reference_controls_transport": False,
            "post_trace_reconciliation": False,
        }
        if (
            canonical != self.canonical_payload_json
            or self.source_state_id != expected_id
            or payload.get("schema_id") != PHYSICAL_SOURCE_STATE_SCHEMA_ID
            or payload.get("schema_version")
            != PHYSICAL_SOURCE_STATE_SCHEMA_VERSION
            or not isinstance(payload.get("system_id"), str)
            or not payload.get("system_id")
            or payload.get("stage_role") != "baseline_ppfd"
            or payload.get("plants_in_scientific_transport") is not False
            or payload.get("handoff_contract") != expected_handoff
            or not isinstance(payload.get("source_operation"), dict)
            or not payload.get("source_operation")
            or any(
                not _valid_sha256(payload.get(name))
                for name in (
                    "layout_identity_sha256",
                    "full_output_schedule_sha256",
                    "operating_point_sha256",
                )
            )
        ):
            raise PhysicalSourceStateError(
                "physical source state identity or baseline role is invalid."
            )

    def payload(self) -> dict[str, object]:
        decoded = json.loads(self.canonical_payload_json)
        if not isinstance(decoded, dict):  # pragma: no cover - guarded above
            raise PhysicalSourceStateError("physical source state is invalid.")
        return decoded

    def to_dict(self) -> dict[str, object]:
        return self.payload() | {"source_state_id": self.source_state_id}


def validate_physical_source_state_payload(
    payload: Mapping[str, object],
) -> PhysicalSourceState:
    """Rebuild and validate a serialized source-state artifact."""

    source_state_id = payload.get("source_state_id")
    without_id = {
        str(key): value for key, value in payload.items() if key != "source_state_id"
    }
    if not isinstance(source_state_id, str):
        raise PhysicalSourceStateError("physical source-state ID is missing.")
    return PhysicalSourceState(_canonical_json(without_id), source_state_id)


def hash_json(payload: object) -> str:
    """Expose the canonical hash used by source-state publication validators."""

    return _hash_json(payload)


def _hash_json(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PhysicalSourceStateError(
            "physical source state must be finite and JSON-serializable."
        ) from exc


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


__all__ = [
    "PHYSICAL_SOURCE_STATE_SCHEMA_ID",
    "PHYSICAL_SOURCE_STATE_SCHEMA_VERSION",
    "PhysicalSourceState",
    "PhysicalSourceStateError",
    "hash_json",
    "validate_physical_source_state_payload",
]
