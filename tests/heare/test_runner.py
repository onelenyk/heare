"""The runner table: finding the CLI, reading its sign-in, and its models.

Every CLI is a shell shim in ``tmp_path``; no real installer is touched and
no probe leaves the machine.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

from heare import keys
from heare.reach import runner

FAKE = Path(__file__).with_name("fake_opencode.py")


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    runner._MODELS_CACHE.clear()
    monkeypatch.setattr(runner, "_EXTRA_DIRS", ())
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    (tmp_path / "home").mkdir()
    (tmp_path / "bin").mkdir()
    yield
    runner._MODELS_CACHE.clear()


def _shim(folder: Path, name: str, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _json_shim(folder: Path, name: str, value: dict) -> Path:
    return _shim(folder, name, f"/bin/cat <<'EOF'\n{json.dumps(value)}\nEOF")


def _server_shim(folder: Path, name: str = "opencode") -> Path:
    """A CLI that is a server: the fake answers the health probe."""
    return _shim(folder, name, f'exec "{sys.executable}" "{FAKE}" "$@"')


# -- resolve and detect ----------------------------------------------------


def test_an_absolute_binary_is_taken_as_it_is(tmp_path):
    shim = _shim(tmp_path / "bin", "runner-cli", "exit 0")
    assert runner.resolve(str(shim)) == str(shim)
    assert runner.resolve(str(tmp_path / "gone")) is None


def test_a_bare_binary_is_found_on_path(tmp_path):
    shim = _shim(tmp_path / "bin", "runner-cli", "exit 0")
    assert runner.resolve("runner-cli") == str(shim)


def test_a_binary_outside_path_is_found_in_the_install_dirs(tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    shim = _shim(extra, "runner-cli", "exit 0")
    monkeypatch.setattr(runner, "_EXTRA_DIRS", (str(extra),))
    assert runner.resolve("runner-cli") == str(shim)


def test_an_unknown_binary_is_not_found():
    assert runner.resolve("no-such-runner-cli") is None


def test_detect_returns_only_known_runners_that_are_installed(tmp_path):
    shim = _shim(tmp_path / "bin", "opencode", "exit 0")
    assert runner.detect() == [
        {"type": "opencode", "binary": "opencode", "path": str(shim)}
    ]


# -- sign-in ---------------------------------------------------------------


def test_opencode_login_reports_provider_names_and_never_values(tmp_path):
    auth = runner.agent_home() / "data" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({
        "deepseek": {"type": "api", "key": "stored-secret"},
        "openrouter": {"key": "another-secret"},
    }), encoding="utf-8")

    state = runner.login("opencode", None)
    assert state["ok"] is True
    assert state["summary"] == "2 providers signed in"
    assert state["detail"] == "deepseek, openrouter"
    assert "stored-secret" not in json.dumps(state)
    assert "another-secret" not in json.dumps(state)


def test_opencode_login_without_a_store_is_a_clear_no():
    state = runner.login("opencode", None)
    assert state["ok"] is False
    assert state["summary"] == "No providers signed in"
    assert "heare-cli-hands login" in state["detail"]


def test_an_unknown_runner_has_no_sign_in_check():
    state = runner.login("claude", None)
    assert state["ok"] is False
    assert "no sign-in check" in state["detail"]


# -- models ----------------------------------------------------------------


def test_opencode_models_are_grouped_and_named_from_the_snapshot(tmp_path):
    shim = _shim(tmp_path / "bin", "opencode", (
        "/bin/cat <<'EOF'\ndeepseek/deepseek-chat\n"
        "openrouter/openai/gpt-4\nnot a model\nEOF"
    ))
    snapshot = runner.agent_home() / "cache" / "opencode" / "models.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(json.dumps({
        "deepseek": {"name": "DeepSeek", "models": {
            "deepseek-chat": {"name": "DeepSeek Chat"},
        }},
    }), encoding="utf-8")

    groups = runner.models("opencode", str(shim))
    assert groups == [
        {"group": "deepseek", "models": [
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek Chat"},
        ]},
        {"group": "openrouter", "models": [
            {"id": "openrouter/openai/gpt-4", "name": "openai/gpt-4"},
        ]},
    ]


def test_opencode_models_are_cached_until_the_ttl_ends(tmp_path, monkeypatch):
    source = tmp_path / "models.txt"
    source.write_text("deepseek/one\n", encoding="utf-8")
    shim = _shim(tmp_path / "bin", "opencode", f"/bin/cat {source}")

    assert runner.models("opencode", str(shim))[0]["models"][0]["id"] == (
        "deepseek/one")
    source.write_text("deepseek/two\n", encoding="utf-8")
    assert runner.models("opencode", str(shim))[0]["models"][0]["id"] == (
        "deepseek/one")

    monkeypatch.setattr(runner, "_MODELS_TTL_S", 0.0)
    assert runner.models("opencode", str(shim))[0]["models"][0]["id"] == (
        "deepseek/two")


def test_opencode_models_fall_back_to_nothing_when_the_cli_fails(tmp_path):
    shim = _shim(tmp_path / "bin", "opencode", "exit 1")
    assert runner.models("opencode", str(shim)) == []


def test_an_unknown_runner_has_no_models():
    assert runner.models("claude", None) == []


def test_the_agents_home_is_its_own_and_not_the_persons(tmp_path):
    person = tmp_path / "home"
    (person / ".local" / "share" / "opencode").mkdir(parents=True)
    (person / ".local" / "share" / "opencode" / "auth.json").write_text(
        json.dumps({"mine": {"key": "secret"}}), encoding="utf-8")

    env = runner.agent_env(runner.agent_home())

    assert Path(env["HOME"]) != person
    assert Path(env["XDG_DATA_HOME"]) != person / ".local" / "share"
    assert Path(env["XDG_CONFIG_HOME"]).is_dir()
    assert Path(env["HOME"]).is_dir()
    # The person's own sign-in is not ours: we have not signed in.
    state = runner.login("opencode", None)
    assert state["ok"] is False


def test_git_keeps_the_persons_identity_in_the_agents_home(tmp_path):
    person = tmp_path / "home"
    person.mkdir(parents=True, exist_ok=True)
    (person / ".gitconfig").write_text(
        "[user]\n\tname = Someone\n", encoding="utf-8")

    env = runner.agent_env(runner.agent_home())

    config = Path(env["HOME"]) / ".gitconfig"
    assert "path = " + str(person / ".gitconfig") in config.read_text(
        encoding="utf-8")


def test_the_ssh_keys_are_reachable_from_the_agents_home(tmp_path):
    person = tmp_path / "home"
    (person / ".ssh").mkdir(parents=True)
    (person / ".ssh" / "id_ed25519").write_text("key", encoding="utf-8")

    env = runner.agent_env(runner.agent_home())

    assert (Path(env["HOME"]) / ".ssh").resolve() == (person / ".ssh").resolve()


# -- environment -----------------------------------------------------------


def test_a_key_is_overlaid_from_the_store(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    keys.store("DEEPSEEK_API_KEY", "stored-value")
    merged = runner.env("opencode", "key", "DEEPSEEK_API_KEY", "", "")
    assert merged["DEEPSEEK_API_KEY"] == "stored-value"


def test_a_login_carries_no_provider_key_for_opencode(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "another-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "cloud-secret")
    merged = runner.env("opencode", "login", "", "", "")
    assert "ANTHROPIC_API_KEY" not in merged
    assert "DEEPSEEK_API_KEY" not in merged
    assert "AWS_SECRET_ACCESS_KEY" not in merged
    assert "PATH" in merged and "HOME" in merged


def test_an_opencode_base_url_is_a_config_patch_not_an_env_var():
    merged = runner.env("opencode", "login", "", "https://gateway.test",
                        "deepseek/deepseek-chat")
    assert "ANTHROPIC_BASE_URL" not in merged
    assert runner.opencode_config_patch(
        "opencode", "https://gateway.test", "deepseek/deepseek-chat"
    ) == {"provider": {"deepseek": {"options": {
        "baseURL": "https://gateway.test",
    }}}}


def test_an_opencode_patch_needs_a_url_and_a_prefixed_model():
    assert runner.opencode_config_patch("opencode", "", "deepseek/x") == {}
    assert runner.opencode_config_patch("opencode", "https://x", "opus") == {}
    assert runner.opencode_config_patch("claude", "https://x", "opus") == {}


# -- health ----------------------------------------------------------------


def _ok_agent(binary: str, env: dict[str, str]) -> runner.Check:
    return {"name": "agent", "ok": True, "detail": "opencode fake answers"}


def test_health_reports_a_missing_binary_and_a_missing_key(monkeypatch):
    monkeypatch.setattr(runner, "_agent_check", _ok_agent)
    state = runner.health("opencode", "gone-cli", "key", "ANTHROPIC_API_KEY", "")
    assert state["ok"] is False
    assert state["checks"][0] == {
        "name": "binary", "ok": False, "detail": "gone-cli was not found",
    }
    assert state["checks"][1]["ok"] is False
    assert "ANTHROPIC_API_KEY" in state["checks"][1]["detail"]


def test_health_is_ok_when_the_binary_the_key_and_the_agent_are_there(
    tmp_path, monkeypatch
):
    _shim(tmp_path / "bin", "opencode", "exit 0")
    keys.store("ANTHROPIC_API_KEY", "stored-value")
    monkeypatch.setattr(runner, "_agent_check", _ok_agent)

    state = runner.health("opencode", "opencode", "key", "ANTHROPIC_API_KEY", "")
    assert state["ok"] is True
    assert [check["ok"] for check in state["checks"]] == [True, True, True]
    assert "stored-value" not in json.dumps(state)


def test_health_is_ok_with_opencode_signed_in(tmp_path, monkeypatch):
    shim = _shim(tmp_path / "bin", "opencode", "exit 0")
    auth = runner.agent_home() / "data" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"deepseek": {"key": "stored-secret"}}),
                    encoding="utf-8")
    monkeypatch.setattr(runner, "_agent_check", _ok_agent)

    state = runner.health("opencode", str(shim), "login", "", "")
    assert state["ok"] is True
    assert [check["ok"] for check in state["checks"]] == [True, True, True]


def test_health_counts_any_http_status_as_reachable(tmp_path, monkeypatch):
    _shim(tmp_path / "bin", "opencode", "exit 0")
    keys.store("ANTHROPIC_API_KEY", "stored-value")
    monkeypatch.setattr(runner, "_agent_check", _ok_agent)

    def not_found(request, timeout=5):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(runner.urllib.request, "urlopen", not_found)
    state = runner.health("opencode", "opencode", "key", "ANTHROPIC_API_KEY",
                          "https://gateway.test")
    assert state["ok"] is True
    assert state["checks"][2]["ok"] is True
    assert "HTTP 404" in state["checks"][2]["detail"]


def test_health_reports_an_unreachable_base_url(tmp_path, monkeypatch):
    _shim(tmp_path / "bin", "opencode", "exit 0")
    keys.store("ANTHROPIC_API_KEY", "stored-value")
    monkeypatch.setattr(runner, "_agent_check", _ok_agent)

    def unreachable(request, timeout=5):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(runner.urllib.request, "urlopen", unreachable)
    state = runner.health("opencode", "opencode", "key", "ANTHROPIC_API_KEY",
                          "https://gateway.test")
    assert state["ok"] is False
    assert state["checks"][2]["ok"] is False
    assert "unreachable" in state["checks"][2]["detail"]


def test_the_check_starts_the_agent_and_asks_if_it_is_up(tmp_path):
    """The smoke test the hands were missing: the button asks the agent."""
    _server_shim(tmp_path / "bin")

    state = runner.health("opencode", str(tmp_path / "bin" / "opencode"),
                          "login", "", "")
    agent = [check for check in state["checks"] if check["name"] == "agent"]
    assert len(agent) == 1
    assert agent[0]["ok"] is True, agent[0]["detail"]
    assert "fake answers" in agent[0]["detail"]


def test_the_check_says_so_when_the_agent_will_not_start(tmp_path):
    shim = _shim(tmp_path / "bin", "opencode", "exit 3")

    state = runner.health("opencode", str(shim), "login", "", "")
    agent = [check for check in state["checks"] if check["name"] == "agent"]
    assert agent[0]["ok"] is False, agent[0]["detail"]
    assert "exited" in agent[0]["detail"]
