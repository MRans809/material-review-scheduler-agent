from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schedule_agent.agent import ScheduleAgent
from schedule_agent.decision import DecisionProviderError, MockDecisionModel
from schedule_agent.models import Action, Decision, RunRequest
from schedule_agent.tools import ToolRuntime


def _request(
    material_id: str,
    requested_at: str = "2026-11-01T17:00:00+08:00",
    **extra: Any,
) -> RunRequest:
    return RunRequest.from_dict(
        {
            "material_id": material_id,
            "requested_at": requested_at,
            **extra,
        }
    )


def _agent(data_dir: Path, max_steps: int = 8) -> ScheduleAgent:
    return ScheduleAgent(
        MockDecisionModel(),
        ToolRuntime(data_dir, data_dir / "test-drafts.json"),
        max_steps=max_steps,
    )


def test_normal_schedule_saves_draft_without_publishing(data_dir: Path) -> None:
    run = _agent(data_dir).run(_request("M01"))

    assert run.output["task_status"] == "completed"
    assert run.output["review_status"] == "pass"
    assert run.output["suggested_slot"] == "2026-11-01T17:00:00+08:00"
    assert run.output["draft_save"]["status"] == "saved"
    assert run.output["published"] is False


def test_missing_metadata_blocks_before_slot_query(data_dir: Path) -> None:
    run = _agent(data_dir).run(_request("M02"))

    assert run.output["task_status"] == "blocked"
    assert run.output["review_status"] == "missing_information"
    assert run.output["missing_fields"] == ["rights_status"]
    assert run.output["draft_save"]["status"] == "not_saved"
    actions = [event["decision"]["action"] for event in run.trace["events"]]
    assert "query_slots" not in actions


def test_rule_risk_blocks_draft(data_dir: Path) -> None:
    run = _agent(data_dir).run(_request("M03"))

    assert run.output["task_status"] == "blocked"
    assert run.output["review_status"] == "risk"
    assert run.output["risk_reasons"][0]["code"] == "PROHIBITED_CLAIM"
    assert "manual_risk_review" in run.output["pending_items"]


def test_conflict_suggests_alternative_and_waits_for_confirmation(
    data_dir: Path,
) -> None:
    run = _agent(data_dir).run(
        _request("M01", "2026-11-01T18:00:00+08:00")
    )

    assert run.output["task_status"] == "pending_confirmation"
    assert run.output["suggested_slot"] == "2026-11-01T19:00:00+08:00"
    assert run.output["pending_items"] == ["confirm_alternative_slot"]
    assert run.output["draft_save"]["status"] == "not_saved"


def test_confirmed_alternative_is_rechecked_then_saved(data_dir: Path) -> None:
    run = _agent(data_dir).run(
        _request(
            "M01",
            "2026-11-01T18:00:00+08:00",
            confirmed_slot="2026-11-01T19:00:00+08:00",
            request_id="CONFIRM-001",
        )
    )

    assert run.output["task_status"] == "completed"
    assert run.output["suggested_slot"] == "2026-11-01T19:00:00+08:00"
    assert run.output["draft_save"]["status"] == "saved"
    slot_queries = [
        event["decision"]["arguments"]["slot"]
        for event in run.trace["events"]
        if event["decision"] and event["decision"]["action"] == "query_slots"
    ]
    assert slot_queries == [
        "2026-11-01T18:00:00+08:00",
        "2026-11-01T19:00:00+08:00",
    ]


def test_duplicate_execution_returns_one_existing_draft(data_dir: Path) -> None:
    agent = _agent(data_dir)
    request = _request("M04", "2026-11-01T20:00:00+08:00")

    first = agent.run(request)
    second = agent.run(request)

    assert first.output["draft_save"]["status"] == "saved"
    assert second.output["draft_save"]["status"] == "already_exists"
    assert first.output["draft_save"]["draft_id"] == second.output["draft_save"][
        "draft_id"
    ]
    state = json.loads((data_dir / "test-drafts.json").read_text(encoding="utf-8"))
    assert len(state["drafts"]) == 1


def test_save_failure_is_not_reported_as_success(data_dir: Path) -> None:
    run = _agent(data_dir).run(
        _request(
            "M05",
            "2026-11-01T21:00:00+08:00",
            simulate_save_failure=True,
        )
    )

    assert run.output["task_status"] == "failed"
    assert run.output["draft_save"]["status"] == "failed"
    assert run.output["draft_save"]["error_code"] == "save_failed"


def test_changed_tool_observation_changes_next_action(data_dir: Path) -> None:
    request = _request("M01", "2026-11-01T18:00:00+08:00")
    conflicted = _agent(data_dir).run(request)

    schedule_path = data_dir / "schedule.json"
    schedule_path.write_text('{"entries": []}\n', encoding="utf-8")
    available = ScheduleAgent(
        MockDecisionModel(),
        ToolRuntime(data_dir, data_dir / "second-state.json"),
    ).run(request)

    assert conflicted.output["task_status"] == "pending_confirmation"
    assert available.output["task_status"] == "completed"
    assert available.output["draft_save"]["status"] == "saved"


@dataclass
class RepeatReadModel:
    name: str = "repeat-read-test"

    def decide(
        self,
        request: RunRequest,
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> Decision:
        return Decision(
            Action.READ_MATERIAL,
            {"material_id": request.material_id},
            "Repeat a valid action to exercise the step limit.",
        )


def test_step_limit_returns_explicit_incomplete_status(data_dir: Path) -> None:
    agent = ScheduleAgent(
        RepeatReadModel(),
        ToolRuntime(data_dir, data_dir / "limit-state.json"),
        max_steps=2,
    )
    run = agent.run(_request("M01"))

    assert run.output["task_status"] == "incomplete"
    assert run.output["steps_used"] == 2
    assert run.trace["events"][-1]["next_action"] == "end:max_steps"


def test_tool_rejects_unconfirmed_alternative(data_dir: Path) -> None:
    tools = ToolRuntime(data_dir, data_dir / "boundary-state.json")
    request = _request("M01", "2026-11-01T18:00:00+08:00")
    review = {
        "review_status": "pass",
        "rule_version": "2026-10-01",
    }

    result = tools.dispatch(
        Action.SAVE_DRAFT,
        {"slot": "2026-11-01T19:00:00+08:00"},
        request,
        review,
    )

    assert result.ok is False
    assert result.error_code == "confirmation_required"


@dataclass
class FailAfterSaveModel:
    name: str = "fail-after-save-test"

    def decide(
        self,
        request: RunRequest,
        observations: list[dict[str, Any]],
        step: int,
        max_steps: int,
    ) -> Decision:
        if observations and observations[-1]["action"] == "save_draft":
            raise DecisionProviderError("summary timeout")
        return MockDecisionModel().decide(request, observations, step, max_steps)


def test_confirmed_save_remains_completed_if_final_model_summary_fails(
    data_dir: Path,
) -> None:
    agent = ScheduleAgent(
        FailAfterSaveModel(),
        ToolRuntime(data_dir, data_dir / "post-save-error-state.json"),
    )

    run = agent.run(_request("M01"))

    assert run.output["task_status"] == "completed"
    assert run.output["draft_save"]["status"] == "saved"
    assert run.output["completion_reason"].startswith("Draft save was confirmed")
    assert "summary timeout" in run.output["completion_reason"]
