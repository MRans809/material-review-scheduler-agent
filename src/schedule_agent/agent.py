from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .decision import DecisionProvider, DecisionProviderError
from .models import Action, Decision, RunRequest, TaskStatus
from .tools import ToolRuntime


@dataclass
class AgentRun:
    output: dict[str, Any]
    trace: dict[str, Any]


class ScheduleAgent:
    def __init__(
        self,
        decision_provider: DecisionProvider,
        tools: ToolRuntime,
        max_steps: int = 8,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.decision_provider = decision_provider
        self.tools = tools
        self.max_steps = max_steps

    def run(self, request: RunRequest) -> AgentRun:
        run_id = f"RUN-{uuid4().hex[:12].upper()}"
        observations: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        finish_decision: Decision | None = None
        terminal_reason = "maximum step limit reached"

        for step in range(1, self.max_steps + 1):
            try:
                decision = self.decision_provider.decide(
                    request=request,
                    observations=observations,
                    step=step,
                    max_steps=self.max_steps,
                )
            except DecisionProviderError as exc:
                if events:
                    events[-1]["next_action"] = "model_error"
                events.append(
                    {
                        "step": step,
                        "decision": None,
                        "tool_result": None,
                        "next_action": "end",
                        "error": str(exc),
                    }
                )
                terminal_reason = str(exc)
                output = self._build_output(
                    request,
                    observations,
                    TaskStatus.FAILED,
                    terminal_reason,
                    step,
                )
                return AgentRun(
                    output=output,
                    trace=self._build_trace(run_id, request, events, output),
                )

            if events:
                events[-1]["next_action"] = decision.action.value

            if decision.action is Action.FINISH:
                finish_decision = decision
                events.append(
                    {
                        "step": step,
                        "decision": decision.to_dict(),
                        "tool_result": None,
                        "next_action": "end",
                    }
                )
                requested_status = _parse_task_status(
                    decision.arguments.get("status")
                )
                terminal_reason = str(
                    decision.arguments.get("reason") or decision.reason_summary
                )
                output = self._build_output(
                    request,
                    observations,
                    requested_status,
                    terminal_reason,
                    step,
                )
                return AgentRun(
                    output=output,
                    trace=self._build_trace(run_id, request, events, output),
                )

            review = _latest_successful_data(observations, Action.CHECK_RULES)
            tool_result = self.tools.dispatch(
                decision.action, decision.arguments, request, review
            )
            observation = {
                "action": decision.action.value,
                "arguments": decision.arguments,
                "result": tool_result.to_dict(),
            }
            observations.append(observation)
            events.append(
                {
                    "step": step,
                    "decision": decision.to_dict(),
                    "tool_result": tool_result.to_dict(),
                    "next_action": None,
                }
            )

        if events:
            events[-1]["next_action"] = "end:max_steps"
        output = self._build_output(
            request,
            observations,
            TaskStatus.INCOMPLETE,
            terminal_reason,
            self.max_steps,
        )
        return AgentRun(
            output=output,
            trace=self._build_trace(run_id, request, events, output),
        )

    def _build_output(
        self,
        request: RunRequest,
        observations: list[dict[str, Any]],
        requested_status: TaskStatus,
        terminal_reason: str,
        steps_used: int,
    ) -> dict[str, Any]:
        review = _latest_successful_data(observations, Action.CHECK_RULES) or {}
        review_status = review.get("review_status", "unchecked")
        missing_fields = review.get("missing_fields", [])
        risks = list(review.get("risks", []))
        risks.extend(
            {
                "code": "MISSING_FIELD",
                "message": f"required field is missing: {field}",
            }
            for field in missing_fields
        )

        requested_query = _slot_observation(observations, request.requested_at)
        confirmed_query = (
            _slot_observation(observations, request.confirmed_slot)
            if request.confirmed_slot
            else None
        )
        suggested_slot: str | None = None
        pending_items: list[str] = []
        if requested_query:
            query_data = requested_query.get("data", {})
            if query_data.get("status") in {"available", "reserved_by_same_request"}:
                suggested_slot = request.requested_at
            else:
                alternatives = query_data.get("available_alternatives", [])
                suggested_slot = (
                    request.confirmed_slot
                    if confirmed_query
                    and confirmed_query.get("data", {}).get("status")
                    == "available"
                    else (alternatives[0] if alternatives else None)
                )
                if not request.confirmed_slot:
                    pending_items.append("confirm_alternative_slot")
                elif confirmed_query and confirmed_query.get("data", {}).get(
                    "status"
                ) == "occupied":
                    pending_items.append("confirm_another_slot")

        if missing_fields:
            pending_items.extend(f"provide:{field}" for field in missing_fields)
        if review_status == "risk":
            pending_items.append("manual_risk_review")

        save_result = _latest_result(observations, Action.SAVE_DRAFT)
        if save_result is None:
            draft_save = {
                "status": "not_saved",
                "draft_id": None,
                "message": "no draft save was confirmed",
            }
        elif save_result.get("ok"):
            save_data = save_result.get("data", {})
            draft_save = {
                "status": save_data.get("save_status", "saved"),
                "draft_id": save_data.get("draft_id"),
                "message": save_result.get("summary"),
            }
        else:
            draft_save = {
                "status": "failed",
                "draft_id": None,
                "message": save_result.get("summary"),
                "error_code": save_result.get("error_code"),
            }

        status = self._enforce_terminal_status(
            requested_status,
            review_status,
            requested_query,
            save_result,
        )
        return {
            "task_status": status.value,
            "material_id": request.material_id,
            "review_status": review_status,
            "risk_reasons": risks,
            "missing_fields": missing_fields,
            "requested_slot": request.requested_at,
            "suggested_slot": suggested_slot,
            "pending_items": pending_items,
            "draft_save": draft_save,
            "published": False,
            "completion_reason": terminal_reason,
            "steps_used": steps_used,
            "step_limit": self.max_steps,
        }

    @staticmethod
    def _enforce_terminal_status(
        requested_status: TaskStatus,
        review_status: str,
        requested_query: dict[str, Any] | None,
        save_result: dict[str, Any] | None,
    ) -> TaskStatus:
        if save_result is not None:
            return (
                TaskStatus.COMPLETED
                if save_result.get("ok")
                else TaskStatus.FAILED
            )
        if review_status in {"missing_information", "risk"}:
            return TaskStatus.BLOCKED
        if requested_query and requested_query.get("data", {}).get("status") == "occupied":
            return TaskStatus.PENDING_CONFIRMATION
        if requested_status is TaskStatus.COMPLETED:
            return TaskStatus.INCOMPLETE
        return requested_status

    def _build_trace(
        self,
        run_id: str,
        request: RunRequest,
        events: list[dict[str, Any]],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "trace_version": "1.0",
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision_provider": self.decision_provider.name,
            "mock_boundary": (
                "Offline mock validates orchestration only; it does not prove LLM decision quality."
                if self.decision_provider.name == "offline-mock"
                else None
            ),
            "input": request.to_dict(),
            "max_steps": self.max_steps,
            "events": events,
            "final_output": output,
        }


def _parse_task_status(value: Any) -> TaskStatus:
    try:
        return TaskStatus(str(value))
    except ValueError:
        return TaskStatus.FAILED


def _latest_result(
    observations: list[dict[str, Any]], action: Action
) -> dict[str, Any] | None:
    for observation in reversed(observations):
        if observation.get("action") == action.value:
            return observation.get("result")
    return None


def _latest_successful_data(
    observations: list[dict[str, Any]], action: Action
) -> dict[str, Any] | None:
    result = _latest_result(observations, action)
    if result and result.get("ok"):
        return result.get("data", {})
    return None


def _slot_observation(
    observations: list[dict[str, Any]], slot: str | None
) -> dict[str, Any] | None:
    if slot is None:
        return None
    for observation in reversed(observations):
        if observation.get("action") != Action.QUERY_SLOTS.value:
            continue
        result = observation.get("result", {})
        if result.get("data", {}).get("slot") == slot:
            return result
    return None
