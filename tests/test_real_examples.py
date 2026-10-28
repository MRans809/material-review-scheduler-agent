from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_model_outputs_match_documented_acceptance_results() -> None:
    expected = {
        "api-normal.json": ("completed", "pass", "saved"),
        "api-missing-metadata.json": (
            "blocked",
            "missing_information",
            "not_saved",
        ),
        "api-rule-risk.json": ("blocked", "risk", "not_saved"),
        "api-slot-conflict.json": (
            "pending_confirmation",
            "pass",
            "not_saved",
        ),
        "api-slot-confirmed.json": ("completed", "pass", "saved"),
        "api-duplicate-replay.json": ("completed", "pass", "already_exists"),
        "api-save-failure.json": ("failed", "pass", "failed"),
    }
    for file_name, statuses in expected.items():
        output = _read_json(ROOT / "examples" / "outputs" / file_name)
        assert (
            output["task_status"],
            output["review_status"],
            output["draft_save"]["status"],
        ) == statuses
        assert output["published"] is False

    first = _read_json(ROOT / "examples" / "outputs" / "api-duplicate-first.json")
    replay = _read_json(
        ROOT / "examples" / "outputs" / "api-duplicate-replay.json"
    )
    assert first["draft_save"]["draft_id"] == replay["draft_save"]["draft_id"]


def test_real_model_traces_are_attributed_and_have_next_actions() -> None:
    for trace_path in sorted((ROOT / "examples" / "traces").glob("api-*.json")):
        trace = _read_json(trace_path)
        assert trace["decision_provider"] == "openai-compatible"
        assert trace["model"] == "qwen3.5-plus"
        assert trace["mock_boundary"] is None
        assert trace["events"]
        assert all(event["next_action"] for event in trace["events"])


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)
