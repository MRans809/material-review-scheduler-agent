from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .agent import ScheduleAgent
from .decision import MockDecisionModel, OpenAICompatibleDecisionModel
from .models import RunRequest
from .tools import ToolRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schedule-agent",
        description="Review a material and save a publication schedule draft.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run one bounded agent task")
    run_parser.add_argument("--input", type=Path, required=True)
    run_parser.add_argument("--model", choices=("mock", "api"), default="mock")
    run_parser.add_argument("--max-steps", type=int, default=8)
    run_parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    run_parser.add_argument(
        "--state-file", type=Path, default=PROJECT_ROOT / "data" / "drafts.local.json"
    )
    run_parser.add_argument("--trace-out", type=Path)
    run_parser.add_argument("--output-out", type=Path)
    run_parser.add_argument("--api-key", default=os.getenv("MODEL_API_KEY"))
    run_parser.add_argument(
        "--base-url",
        default=os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"),
    )
    run_parser.add_argument("--model-name", default=os.getenv("MODEL_NAME"))
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("unsupported command")

    try:
        raw_input = _read_json(args.input)
        request = RunRequest.from_dict(raw_input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if args.model == "mock":
        provider = MockDecisionModel()
    else:
        if not args.api_key or not args.model_name:
            parser.error(
                "api mode requires MODEL_API_KEY and MODEL_NAME (or matching flags)"
            )
        provider = OpenAICompatibleDecisionModel(
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model_name,
        )

    try:
        tools = ToolRuntime(args.data_dir, args.state_file)
        run = ScheduleAgent(provider, tools, max_steps=args.max_steps).run(request)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    rendered_output = json.dumps(run.output, ensure_ascii=False, indent=2)
    print(rendered_output)
    if args.output_out:
        _write_json(args.output_out, run.output)
    if args.trace_out:
        _write_json(args.trace_out, run.trace)
    return 0


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
