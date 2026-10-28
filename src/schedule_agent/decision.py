from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .models import Action, Decision, RunRequest


SYSTEM_PROMPT = """You are a material review and publication draft scheduling agent.
Choose exactly one next action from the allowlist. Business tools are the only source
of truth. Never say an item was published: this system can save drafts only.

Rules:
1. Read the material, check rules, and query the requested slot before saving.
2. If metadata is missing or a rule risk exists, stop with blocked status.
3. Treat requested_at as a hard requirement. If occupied, suggest an available
   alternative and finish with pending_confirmation unless confirmed_slot exactly
   matches the available alternative.
4. Save only after a passing review and an available requested or confirmed slot.
   A slot marked reserved_by_same_request may be saved again to verify idempotency.
5. On a tool error, adjust only when another valid tool action can resolve it;
   otherwise finish with failed status.
6. reason_summary must be a short, user-safe rationale, not hidden reasoning.

Allowed actions and arguments:
- read_material: {"material_id": string}
- check_rules: {"material_id": string}
- query_slots: {"slot": ISO-8601 datetime}
- save_draft: {"slot": ISO-8601 datetime}
- finish: {"status": "completed|pending_confirmation|blocked|failed|incomplete",
           "reason": string}

Return one JSON object only:
{"action": string, "arguments": object, "reason_summary": string}
"""


class DecisionProvider(Protocol):
    name: str

    def decide(
        self,
        request: RunRequest,
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> Decision: ...


class DecisionProviderError(RuntimeError):
    pass


@dataclass
class MockDecisionModel:
    """Observation-driven offline policy; it validates flow, not LLM quality."""

    name: str = "offline-mock"

    def decide(
        self,
        request: RunRequest,
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> Decision:
        material_result = _latest_result(observations, Action.READ_MATERIAL)
        if material_result is None:
            return Decision(
                Action.READ_MATERIAL,
                {"material_id": request.material_id},
                "Material metadata is needed before review.",
            )
        if not material_result["ok"]:
            return _finish("blocked", "The requested material could not be loaded.")

        review_result = _latest_result(observations, Action.CHECK_RULES)
        if review_result is None:
            return Decision(
                Action.CHECK_RULES,
                {"material_id": request.material_id},
                "The loaded material must be checked against the active rules.",
            )
        if not review_result["ok"]:
            return _finish("failed", "The rule check tool did not complete.")
        review_status = review_result["data"].get("review_status")
        if review_status in {"missing_information", "risk"}:
            return _finish(
                "blocked",
                "The observed review result requires missing data or manual handling.",
            )

        requested_result = _slot_result(observations, request.requested_at)
        if requested_result is None:
            return Decision(
                Action.QUERY_SLOTS,
                {"slot": request.requested_at},
                "The requested hard time must be checked for conflicts.",
            )
        if not requested_result["ok"]:
            return _finish("failed", "The requested slot could not be checked.")

        chosen_slot = request.requested_at
        if requested_result["data"].get("status") == "occupied":
            alternatives = requested_result["data"].get(
                "available_alternatives", []
            )
            if not request.confirmed_slot:
                suggestion = alternatives[0] if alternatives else "no available slot"
                return _finish(
                    "pending_confirmation",
                    f"The requested slot is occupied; user confirmation is needed for {suggestion}.",
                )
            confirmed_result = _slot_result(observations, request.confirmed_slot)
            if confirmed_result is None:
                return Decision(
                    Action.QUERY_SLOTS,
                    {"slot": request.confirmed_slot},
                    "The user-confirmed alternative must be checked before saving.",
                )
            if not confirmed_result["ok"]:
                return _finish("failed", "The confirmed slot could not be checked.")
            if confirmed_result["data"].get("status") == "occupied":
                return _finish(
                    "pending_confirmation",
                    "The confirmed alternative is also occupied; another confirmation is required.",
                )
            chosen_slot = request.confirmed_slot

        save_result = _latest_result(observations, Action.SAVE_DRAFT)
        if save_result is None:
            return Decision(
                Action.SAVE_DRAFT,
                {"slot": chosen_slot},
                "Review passed and the selected slot is available, so a draft can be saved.",
            )
        if save_result["ok"]:
            return _finish(
                "completed", "The draft store confirmed the save or idempotent replay."
            )
        return _finish(
            "failed", "The draft store reported a failure, so completion cannot be claimed."
        )


@dataclass
class OpenAICompatibleDecisionModel:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 30.0
    name: str = "openai-compatible"

    def decide(
        self,
        request: RunRequest,
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> Decision:
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "request": request.to_dict(),
                            "observations": observations,
                            "step": step,
                            "max_steps": max_steps,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        http_request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                http_request, timeout=self.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            raw_decision = json.loads(content)
            return Decision.from_dict(raw_decision)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DecisionProviderError(f"model request failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DecisionProviderError(
                "model response did not contain a valid decision JSON object"
            ) from exc


def _latest_result(
    observations: list[dict[str, Any]], action: Action
) -> dict[str, Any] | None:
    for observation in reversed(observations):
        if observation.get("action") == action.value:
            return observation.get("result")
    return None


def _slot_result(
    observations: list[dict[str, Any]], slot: str
) -> dict[str, Any] | None:
    for observation in reversed(observations):
        if observation.get("action") != Action.QUERY_SLOTS.value:
            continue
        result = observation.get("result", {})
        if result.get("data", {}).get("slot") == slot:
            return result
    return None


def _finish(status: str, reason: str) -> Decision:
    return Decision(
        Action.FINISH,
        {"status": status, "reason": reason},
        reason,
    )
