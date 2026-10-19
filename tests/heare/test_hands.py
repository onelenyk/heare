"""The hands body: the one line it shows of the ACP wire, and how it starts."""

from __future__ import annotations

from pathlib import Path

from heare import hands, keys
from heare.config import Settings


def test_a_prompt_is_one_readable_line():
    line = hands._line("out", {
        "method": "session/prompt",
        "params": {"prompt": [{"type": "text", "text": "перевір теку"}]},
    })
    assert line == "→ prompt · перевір теку"


def test_an_update_names_what_the_agent_is_doing():
    assert hands._line("in", {"method": "session/update", "params": {
        "update": {"sessionUpdate": "tool_call", "title": "bash"}}}) == (
        "← tool_call · bash")
    assert hands._line("in", {"method": "session/update", "params": {
        "update": {"sessionUpdate": "agent_message_chunk",
                   "content": {"text": "Готово."}}}}) == (
        "← agent_message_chunk · Готово.")


def test_an_answer_is_a_short_mark():
    assert hands._line("in", {"id": 3, "result": {}}) == "← ok #3"
    assert hands._line("in", {"id": 4, "error": {"message": "no"}}) == (
        "← error · no")


def test_a_long_line_is_cut_at_the_hands_own_width():
    line = hands._line("out", {"method": "session/prompt", "params": {
        "prompt": [{"text": "я" * 200}]}})
    assert line.endswith("…") and len(line) < 90


def test_the_price_says_what_the_turn_cost():
    assert hands._price(0.000152, 39392, 1000000) == "$0.0002 · 39k / 1M"
    assert hands._price(0.0, 0, 0) == ""
    assert hands._price(0.5, 900, 200000) == "$0.5000 · 900 / 200k"


def _shim(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        path=tmp_path / "config.toml",
        executor_cwd=tmp_path / "work",
        **overrides,
    )


def test_the_runner_settings_become_the_agents_launch(tmp_path, monkeypatch):
    seen: dict = {}

    class Captured:
        def __init__(self, known, **kw):
            seen["known"] = known
            seen.update(kw)

    monkeypatch.setattr(hands, "Agent", Captured)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(hands.runner, "_EXTRA_DIRS", ())
    keys.store("DEEPSEEK_API_KEY", "stored-value")
    shim = _shim(tmp_path / "bin", "opencode")

    settings = _settings(
        tmp_path,
        executor="opencode",
        executor_binary=str(shim),
        executor_model="deepseek/deepseek-chat",
        executor_auth="key",
        executor_auth_env="DEEPSEEK_API_KEY",
        executor_base_url="https://gateway.test",
    )
    agent = hands._agent(settings, None, [{"name": "heare"}])

    assert agent is not None
    assert seen["known"].name == "opencode"
    assert seen["command"] == [str(shim), "acp"]
    assert seen["env"]["DEEPSEEK_API_KEY"] == "stored-value"
    assert seen["model"] == "deepseek/deepseek-chat"
    assert seen["config_patch"] == {
        "provider": {"deepseek": {"options": {"baseURL": "https://gateway.test"}}},
    }
    assert seen["mcp_servers"] == [{"name": "heare"}]


def test_claude_starts_the_adapter_through_npx(tmp_path, monkeypatch):
    seen: dict = {}

    class Captured:
        def __init__(self, known, **kw):
            seen.update(kw)

    monkeypatch.setattr(hands, "Agent", Captured)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(hands.runner, "_EXTRA_DIRS", ())
    npx = _shim(tmp_path / "bin", "npx")
    claude = _shim(tmp_path / "bin", "claude")

    settings = _settings(tmp_path, executor="claude",
                        executor_binary=str(claude))
    assert hands._agent(settings, None, []) is not None
    assert seen["command"] == [
        str(npx), "-y", "@agentclientprotocol/claude-agent-acp@0.81.2",
    ]


def test_a_missing_binary_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(hands.runner, "_EXTRA_DIRS", ())
    settings = _settings(tmp_path, executor="opencode",
                        executor_binary="no-such-opencode")
    assert hands._agent(settings, None, []) is None
    assert "no-such-opencode" in capsys.readouterr().err


def test_an_unknown_runner_type_is_refused(tmp_path, capsys):
    settings = _settings(tmp_path, executor="hosted")
    assert hands._agent(settings, None, []) is None
    assert "hosted" in capsys.readouterr().err


def _named(ui: list[dict]) -> dict[str, dict]:
    return {c["name"]: c for c in ui}


def test_the_hands_declare_their_runner_as_their_own_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(hands.runner, "_EXTRA_DIRS", ())
    monkeypatch.setattr(hands.runner, "login", lambda kind, binary: {
        "ok": True, "summary": "deepseek", "detail": ""})
    monkeypatch.setattr(hands.runner, "models", lambda kind, binary: [
        {"group": "deepseek", "models": [{"id": "deepseek/deepseek-chat",
                                          "name": "DeepSeek"}]}])
    shim = _shim(tmp_path / "bin", "opencode")

    ui = _named(hands._settings_ui(_settings(tmp_path, executor="opencode")))

    assert all(c["group"] == "settings" for c in ui.values())
    assert ui["type"]["value"] == "opencode"
    assert {o["value"] for o in ui["type"]["options"]} == set(hands.runner.KNOWN)
    assert ui["found"]["value"] == str(shim)
    assert [o["value"] for o in ui["model"]["options"]] == [
        "", "deepseek/deepseek-chat"]
    assert ui["login"]["value"] == "deepseek"
    assert "auth_env" not in ui


def test_a_key_sign_in_shows_the_key_and_whether_it_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "models", lambda kind, binary: [])
    keys.store("ANTHROPIC_API_KEY", "stored-value")

    ui = _named(hands._settings_ui(_settings(
        tmp_path, executor="claude", executor_auth="key",
        executor_auth_env="ANTHROPIC_API_KEY")))

    assert ui["auth_env"]["value"] == "ANTHROPIC_API_KEY"
    assert ui["key"]["value"] == "є"
    assert "login" not in ui


def test_without_a_model_list_the_model_is_typed(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "models", lambda kind, binary: [])
    monkeypatch.setattr(hands.runner, "login", lambda kind, binary: {
        "ok": False, "summary": "—", "detail": ""})

    ui = _named(hands._settings_ui(_settings(
        tmp_path, executor="opencode", executor_model="x/y")))

    assert ui["model"]["kind"] == "text"
    assert ui["model"]["value"] == "x/y"


def test_a_control_becomes_a_setting_only_when_the_value_is_one_it_takes():
    assert hands._setting("type", "claude") == ("executor", "claude")
    assert hands._setting("model", " a/b ") == ("executor_model", "a/b")
    assert hands._setting("auth_env", "DEEPSEEK_API_KEY") == (
        "executor_auth_env", "DEEPSEEK_API_KEY")
    assert hands._setting("type", "hosted") is None
    assert hands._setting("auth", "password") is None
    assert hands._setting("auth_env", "HOME") is None
    assert hands._setting("taking", "yes") is None
    assert hands._setting("binary", 3) is None


def test_the_check_is_one_line_per_need(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "health", lambda *a: {"ok": False, "checks": [
        {"name": "binary", "ok": True, "detail": "/bin/opencode"},
        {"name": "auth", "ok": False, "detail": "no sign-in"}]})

    assert hands._checks(_settings(tmp_path)) == (
        "✓ binary: /bin/opencode\n✗ auth: no sign-in")
