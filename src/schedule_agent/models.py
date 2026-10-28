from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Action(str, Enum):
    READ_MATERIAL = "read_material"
    CHECK_RULES = "check_rules"
    QUERY_SLOTS = "query_slots"
    SAVE_DRAFT = "save_draft"
    FINISH = "finish"


class TaskStatus(str, Enum):
    COMPLETED = "completed"
    PENDING_CONFIRMATION = "pending_confirmation"
    BLOCKED = "blocked"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class RunRequest:
    material_id: str
    requested_at: str
    confirmed_slot: str | None = None
    request_id: str | None = None
    simulate_save_failure: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunRequest":
        material_id = str(raw.get("material_id", "")).strip()
        requested_at = str(raw.get("requested_at", "")).strip()
        if not material_id:
            raise ValueError("material_id is required")
        if not requested_at:
            raise ValueError("requested_at is required")
        _parse_datetime(requested_at, "requested_at")

        confirmed_slot = raw.get("confirmed_slot")
        if confirmed_slot is not None:
            confirmed_slot = str(confirmed_slot).strip()
            _parse_datetime(confirmed_slot, "confirmed_slot")

        request_id = raw.get("request_id")
        return cls(
            material_id=material_id,
            requested_at=requested_at,
            confirmed_slot=confirmed_slot,
            request_id=str(request_id) if request_id is not None else None,
            simulate_save_failure=bool(raw.get("simulate_save_failure", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Decision:
    action: Action
    arguments: dict[str, Any] = field(default_factory=dict)
    reason_summary: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Decision":
        if not isinstance(raw, dict):
            raise ValueError("decision must be a JSON object")
        try:
            action = Action(raw.get("action"))
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(item.value for item in Action)
            raise ValueError(f"unsupported action; allowed actions: {allowed}") from exc
        arguments = raw.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("decision.arguments must be a JSON object")
        reason = str(raw.get("reason_summary", "")).strip()
        if not reason:
            raise ValueError("decision.reason_summary is required")
        return cls(action=action, arguments=arguments, reason_summary=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "arguments": self.arguments,
            "reason_summary": self.reason_summary,
        }


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        return payload


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return parsed
