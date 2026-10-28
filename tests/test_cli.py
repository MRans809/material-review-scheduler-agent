from __future__ import annotations

from pathlib import Path

from schedule_agent.cli import _first_env, _load_env_file


def test_load_env_file_supports_qwen_aliases(
    tmp_path: Path, monkeypatch
) -> None:
    for key in ("DASHSCOPE_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DASHSCOPE_API_KEY='test-secret'\n"
        'QWEN_BASE_URL="https://example.test/v1"\n'
        "QWEN_MODEL=qwen-test\n"
        "UNSUPPORTED_KEY=ignored\n",
        encoding="utf-8",
    )

    _load_env_file(env_file)

    assert _first_env("DASHSCOPE_API_KEY") == "test-secret"
    assert _first_env("QWEN_BASE_URL") == "https://example.test/v1"
    assert _first_env("QWEN_MODEL") == "qwen-test"
    assert _first_env("UNSUPPORTED_KEY") is None


def test_load_env_file_does_not_override_existing_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("QWEN_MODEL", "terminal-model")
    env_file = tmp_path / ".env"
    env_file.write_text("QWEN_MODEL=file-model\n", encoding="utf-8")

    _load_env_file(env_file)

    assert _first_env("QWEN_MODEL") == "terminal-model"
