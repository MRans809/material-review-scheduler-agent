from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schedule_agent.agent import ScheduleAgent  # noqa: E402
from schedule_agent.decision import MockDecisionModel  # noqa: E402
from schedule_agent.models import RunRequest  # noqa: E402
from schedule_agent.tools import ToolRuntime  # noqa: E402


def main() -> None:
    cases = [
        ("normal", "normal.json", "normal-state.json"),
        ("slot-conflict", "slot-conflict.json", "conflict-state.json"),
        ("slot-confirmed", "slot-confirmed.json", "confirmed-state.json"),
        ("missing-metadata", "missing-metadata.json", "missing-state.json"),
        ("rule-risk", "rule-risk.json", "risk-state.json"),
        ("save-failure", "save-failure.json", "failure-state.json"),
    ]
    state_dir = ROOT / "examples" / "state"
    mock_state_names = {state_name for _, _, state_name in cases}
    mock_state_names.add("duplicate-state.json")
    for state_name in mock_state_names:
        state_path = state_dir / state_name
        if state_path.exists():
            state_path.unlink()

    for output_name, input_name, state_name in cases:
        _run_case(output_name, input_name, state_name)

    _run_case("duplicate-first", "duplicate.json", "duplicate-state.json")
    _run_case("duplicate-replay", "duplicate.json", "duplicate-state.json")


def _run_case(output_name: str, input_name: str, state_name: str) -> None:
    request = RunRequest.from_dict(
        _read_json(ROOT / "examples" / "inputs" / input_name)
    )
    tools = ToolRuntime(
        ROOT / "data", ROOT / "examples" / "state" / state_name
    )
    run = ScheduleAgent(MockDecisionModel(), tools, max_steps=8).run(request)
    _write_json(ROOT / "examples" / "outputs" / f"{output_name}.json", run.output)
    _write_json(ROOT / "examples" / "traces" / f"{output_name}.json", run.trace)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
