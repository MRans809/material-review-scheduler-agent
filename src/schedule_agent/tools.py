from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Action, RunRequest, ToolResult


class ToolRuntime:
    """Deterministic business tools and policy enforcement for one agent run."""

    _write_lock = threading.Lock()

    def __init__(self, data_dir: Path, state_file: Path | None = None) -> None:
        self.data_dir = data_dir
        self.state_file = state_file or data_dir / "drafts.local.json"
        self.materials = _read_json(data_dir / "materials.json")["materials"]
        self.rules = _read_json(data_dir / "rules.json")
        self.schedule = _read_json(data_dir / "schedule.json")

    def dispatch(
        self,
        action: Action,
        arguments: dict[str, Any],
        request: RunRequest,
        review: dict[str, Any] | None,
    ) -> ToolResult:
        if action is Action.READ_MATERIAL:
            return self._read_material(arguments, request)
        if action is Action.CHECK_RULES:
            return self._check_rules(arguments, request)
        if action is Action.QUERY_SLOTS:
            return self._query_slots(arguments, request)
        if action is Action.SAVE_DRAFT:
            return self._save_draft(arguments, request, review)
        return ToolResult(False, "finish is not a tool", error_code="invalid_tool")

    def _read_material(
        self, arguments: dict[str, Any], request: RunRequest
    ) -> ToolResult:
        scope_error = _validate_material_scope(arguments, request)
        if scope_error:
            return scope_error
        material = next(
            (
                item
                for item in self.materials
                if item.get("material_id") == request.material_id
            ),
            None,
        )
        if material is None:
            return ToolResult(
                False,
                f"material {request.material_id} was not found",
                error_code="material_not_found",
            )
        return ToolResult(
            True,
            f"loaded material {request.material_id}",
            {"material": material},
        )

    def _check_rules(
        self, arguments: dict[str, Any], request: RunRequest
    ) -> ToolResult:
        scope_error = _validate_material_scope(arguments, request)
        if scope_error:
            return scope_error
        material = next(
            (
                item
                for item in self.materials
                if item.get("material_id") == request.material_id
            ),
            None,
        )
        if material is None:
            return ToolResult(
                False,
                "cannot check rules before material exists",
                error_code="material_not_found",
            )

        missing_fields = [
            field
            for field in self.rules["required_fields"]
            if material.get(field) in (None, "", [])
        ]
        risks: list[dict[str, str]] = []
        if material.get("rights_status") not in (None, "cleared"):
            risks.append(
                {
                    "code": "RIGHTS_NOT_CLEARED",
                    "message": "material rights are not cleared",
                }
            )

        combined_text = " ".join(
            [str(material.get("title", ""))]
            + [str(value) for value in material.get("claims", [])]
        ).lower()
        for phrase in self.rules["prohibited_claims"]:
            if phrase.lower() in combined_text:
                risks.append(
                    {
                        "code": "PROHIBITED_CLAIM",
                        "message": f"prohibited claim detected: {phrase}",
                    }
                )

        if missing_fields:
            status = "missing_information"
            summary = "required material metadata is missing"
        elif risks:
            status = "risk"
            summary = "material failed one or more review rules"
        else:
            status = "pass"
            summary = "material passed all configured review rules"

        return ToolResult(
            True,
            summary,
            {
                "review_status": status,
                "missing_fields": missing_fields,
                "risks": risks,
                "rule_version": self.rules["version"],
            },
        )

    def _query_slots(
        self, arguments: dict[str, Any], request: RunRequest
    ) -> ToolResult:
        slot = str(arguments.get("slot", "")).strip()
        if not slot:
            return ToolResult(
                False, "slot is required", error_code="invalid_arguments"
            )
        try:
            parsed_slot = datetime.fromisoformat(slot)
        except ValueError:
            return ToolResult(
                False,
                "slot must be an ISO-8601 datetime",
                error_code="invalid_arguments",
            )
        if parsed_slot.tzinfo is None:
            return ToolResult(
                False,
                "slot must include a UTC offset",
                error_code="invalid_arguments",
            )
        if parsed_slot.date() != datetime.fromisoformat(request.requested_at).date():
            return ToolResult(
                False,
                "candidate slot must be on the requested publication date",
                error_code="hard_requirement_violation",
            )

        state = self._read_state()
        occupied = self._occupied_slots(state)
        conflict = occupied.get(slot)
        own_fingerprint = _draft_fingerprint(request, slot)
        same_request_draft = next(
            (
                item
                for item in state.get("drafts", [])
                if item.get("fingerprint") == own_fingerprint
            ),
            None,
        )
        alternatives = self._find_alternatives(parsed_slot, occupied)
        if same_request_draft:
            status = "reserved_by_same_request"
            summary = (
                f"slot {slot} is already reserved by idempotent draft "
                f"{same_request_draft['draft_id']}"
            )
        elif conflict:
            status = "occupied"
            summary = f"slot {slot} is occupied by {conflict}"
        else:
            status = "available"
            summary = f"slot {slot} is available"
        return ToolResult(
            True,
            summary,
            {
                "slot": slot,
                "status": status,
                "conflict_with": conflict,
                "available_alternatives": alternatives,
            },
        )

    def _save_draft(
        self,
        arguments: dict[str, Any],
        request: RunRequest,
        review: dict[str, Any] | None,
    ) -> ToolResult:
        slot = str(arguments.get("slot", "")).strip()
        if not slot:
            return ToolResult(
                False, "slot is required", error_code="invalid_arguments"
            )
        if review is None or review.get("review_status") != "pass":
            return ToolResult(
                False,
                "a passing rule review is required before saving a draft",
                error_code="review_not_passed",
            )
        if slot != request.requested_at and slot != request.confirmed_slot:
            return ToolResult(
                False,
                "an alternative slot requires explicit user confirmation",
                error_code="confirmation_required",
            )
        if request.simulate_save_failure:
            return ToolResult(
                False,
                "draft store failure was injected for this run",
                error_code="save_failed",
            )

        fingerprint = _draft_fingerprint(request, slot)
        draft_id = f"DRAFT-{fingerprint[:10].upper()}"
        with self._write_lock:
            state = self._read_state()
            existing = next(
                (
                    item
                    for item in state["drafts"]
                    if item.get("fingerprint") == fingerprint
                ),
                None,
            )
            if existing:
                return ToolResult(
                    True,
                    f"idempotent replay returned existing draft {existing['draft_id']}",
                    {
                        "save_status": "already_exists",
                        "draft_id": existing["draft_id"],
                        "slot": existing["slot"],
                    },
                )

            conflict = self._occupied_slots(state).get(slot)
            if conflict:
                return ToolResult(
                    False,
                    f"slot became occupied by {conflict} before save",
                    {"slot": slot, "conflict_with": conflict},
                    error_code="slot_conflict",
                )

            draft = {
                "draft_id": draft_id,
                "fingerprint": fingerprint,
                "material_id": request.material_id,
                "requested_at": request.requested_at,
                "slot": slot,
                "confirmation_required": slot != request.requested_at,
                "confirmation_evidence": request.request_id
                if slot != request.requested_at
                else None,
                "status": "draft",
                "rule_version": review.get("rule_version"),
            }
            state["drafts"].append(draft)
            self._write_state(state)
        return ToolResult(
            True,
            f"saved draft {draft_id}; no publication action was performed",
            {"save_status": "saved", "draft_id": draft_id, "slot": slot},
        )

    def _occupied_slots(
        self, state: dict[str, Any] | None = None
    ) -> dict[str, str]:
        occupied = {
            item["slot"]: item["schedule_id"]
            for item in self.schedule.get("entries", [])
        }
        current_state = state if state is not None else self._read_state()
        for draft in current_state.get("drafts", []):
            occupied[draft["slot"]] = draft["draft_id"]
        return occupied

    def _find_alternatives(
        self, requested: datetime, occupied: dict[str, str]
    ) -> list[str]:
        limits = self.rules["publication_window"]
        alternatives: list[str] = []
        for offset in range(1, 7):
            candidate = requested + timedelta(minutes=60 * offset)
            if candidate.date() != requested.date():
                break
            if not limits["start_hour"] <= candidate.hour < limits["end_hour"]:
                continue
            rendered = candidate.isoformat()
            if rendered not in occupied:
                alternatives.append(rendered)
            if len(alternatives) == 3:
                break
        return alternatives

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"drafts": []}
        return _read_json(self.state_file)

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{self.state_file.name}.",
            suffix=".tmp",
            dir=self.state_file.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, self.state_file)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def _validate_material_scope(
    arguments: dict[str, Any], request: RunRequest
) -> ToolResult | None:
    material_id = str(arguments.get("material_id", "")).strip()
    if not material_id:
        return ToolResult(
            False, "material_id is required", error_code="invalid_arguments"
        )
    if material_id != request.material_id:
        return ToolResult(
            False,
            "model cannot access a material outside the current request",
            error_code="scope_violation",
        )
    return None


def _draft_fingerprint(request: RunRequest, slot: str) -> str:
    canonical = json.dumps(
        {
            "material_id": request.material_id,
            "requested_at": request.requested_at,
            "slot": slot,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return loaded
