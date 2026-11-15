"""The hands body: the one line it shows of the server, and how it starts."""

from __future__ import annotations

import json
from pathlib import Path

from heare import cli_hands as hands, hand, keys
from heare.config import Settings
from heare.reach import agents


def test_a_call_is_one_readable_line():
    assert hands._line("out", {"method": "POST",
                               "path": "/session/ses_1/prompt_async"}) == (
        "→ POST /session/ses_1/prompt_async")


def test_an_event_names_what_the_agent_is_doing():
    assert hands._line("in", {"type": "permission.asked", "properties": {
        "permission": "bash"}}) == "← permission · bash"
    assert hands._line("in", {"type": "message.part.updated", "properties": {
        "part": {"type": "tool", "tool": "bash", "state": {"input": {}}}}}) == (
        "← part · bash")


def test_a_long_line_is_cut_at_the_hands_own_width():
    line = hands._line("in", {"type": "message.part.updated", "properties": {
        "part": {"type": "text", "text": "я" * 200}}})
    assert line.endswith("…") and len(line) < 90


def test_the_price_says_what_the_turn_cost():
    assert hands._price(0.000152, 39392, 1000000) == "$0.0002 · 39k / 1M"
    assert hands._price(0.0, 0, 0) == ""
    assert hands._price(0.5, 900, 200000) == "$0.5000 · 900 / 200k"
    # Free work shows its tokens alone.
    assert hands._price(0.0, 17_373) == "17k"


class _Changed:
    """An agent whose task changed something, as far as the hands look."""

    def __init__(self, found: list[dict]) -> None:
        self._found = found

    async def changes(self) -> list[dict]:
        return self._found


_DIFF = [{"file": "note.txt", "additions": 1, "deletions": 1,
          "patch": "-a\n+b\n", "status": "modified"}]


def test_a_card_the_agent_asks_for_is_html_or_a_link():
    assert hands._card_body({"html": "<b>x</b>", "title": "T"}) == {
        "kind": "html", "html": "<b>x</b>", "title": "T", "say": ""}
    assert hands._card_body({"url": "https://x.test"})["kind"] == "link"
    assert hands._card_body({}) == {}
    assert len(hands._card_body({"html": "x" * 50_000})["html"]) == hands.SHOW_CHARS


async def test_a_card_is_carried_through_the_door():
    shown: list[tuple[int, dict]] = []

    async def show(job: int, body: dict) -> dict:
        shown.append((job, body))
        return {}

    assert await hands._shown(4, {"html": "<b>x</b>"}, show) == "Shown."
    assert shown[0][0] == 4


async def test_a_page_that_will_not_take_the_card_says_so_in_the_agents_words():
    async def show(job: int, body: dict) -> dict:
        raise RuntimeError("no such job")

    assert (await hands._shown(4, {"html": "<b>x</b>"}, show)).startswith(
        "Failed: no such job")


async def test_a_card_with_nothing_in_it_is_refused():
    called: list[int] = []

    async def show(job: int, body: dict) -> dict:
        called.append(job)
        return {}

    assert await hands._shown(4, {}, show) == "Failed: nothing to show."
    assert called == []


async def test_a_card_is_shown_and_one_sentence_is_said():
    shown: list[tuple[int, dict]] = []

    async def show(job: int, body: dict) -> dict:
        shown.append((job, body))
        return {}

    said = await hands._shown_changes(_Changed(_DIFF), 7, show)

    assert said == "Змінив note.txt: +1 −1."
    assert shown[0][0] == 7
    assert shown[0][1]["kind"] == "html"
    assert shown[0][1]["say"] == said


async def test_a_page_that_will_not_take_the_card_does_not_fail_the_work():
    async def show(job: int, body: dict) -> dict:
        raise RuntimeError("the page did not take it")

    said = await hands._shown_changes(_Changed(_DIFF), 7, show)
    assert said == "Змінив note.txt: +1 −1."


async def test_work_that_changed_nothing_says_nothing_about_changes():
    called: list[int] = []

    async def show(job: int, body: dict) -> dict:
        called.append(job)
        return {}

    assert await hands._shown_changes(_Changed([]), 7, show) == ""
    assert called == []


async def test_an_order_the_hands_do_not_know_says_so():
    job = hand.Job(1, "щось", verb="dance")
    assert await hands._verb(None, job, None) == "Не знаю такої команди: dance."


def test_the_card_lists_the_agents_sessions_newest_first():
    import time as clock

    stamp = 1_794_760_000_000
    found = [{"id": "ses_1", "title": "Change b to B",
              "time": {"updated": stamp}}]

    assert hands._session_lines(found) == (
        f"{clock.strftime('%d %b %H:%M', clock.localtime(stamp / 1000))}"
        " · Change b to B")


def test_continue_means_the_session_the_card_picked():
    picked = Settings(path=Path("/dev/null"), executor_session="ses_picked")

    going_on = hand.Job(1, "ще", continues=True)
    assert hands._resume_from(going_on, picked, "ses_last") == "ses_picked"
    assert hands._resume_from(going_on, Settings(path=Path("/dev/null")),
                              "ses_last") == "ses_last"
    named = hand.Job(2, "ще", continues=True, session="ses_named")
    assert hands._resume_from(named, picked, "ses_last") == "ses_named"
    fresh = hand.Job(3, "нове", continues=False)
    assert hands._resume_from(fresh, picked, "ses_last") == ""


def test_the_card_offers_the_sessions_to_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "models", lambda *a, **k: [])
    monkeypatch.setattr(hands.runner, "login", lambda *a, **k: {
        "ok": True, "summary": "—", "detail": ""})

    ui = _named(hands._settings_ui(_settings(tmp_path), [
        {"id": "ses_1", "title": "Change b to B",
         "time": {"updated": 1_794_760_000_000}}]))

    assert ui["session"]["value"] == ""
    assert [o["value"] for o in ui["session"]["options"]] == ["", "ses_1"]
    # N_ marks the words; the page translates them, so the key is English.
    assert ui["session"]["options"][0]["label"] == "the last one"
    assert ui["session"]["options"][1]["label"].startswith("Change b to B · ")


def test_the_work_folder_is_a_card_setting(tmp_path):
    assert hands._setting("cwd", str(tmp_path)) == ("executor_cwd", str(tmp_path))
    assert hands._setting("cwd", "") == ("executor_cwd", "")
    # A folder that is not there is not a folder to work in.
    assert hands._setting("cwd", str(tmp_path / "gone")) is None


def test_the_card_shows_the_work_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "models", lambda *a, **k: [])
    monkeypatch.setattr(hands.runner, "login", lambda *a, **k: {
        "ok": True, "summary": "—", "detail": ""})

    ui = _named(hands._settings_ui(_settings(tmp_path)))

    assert ui["cwd"]["value"] == str(tmp_path / "work")
    assert ui["cwd"]["kind"] == "text"


def test_a_picked_session_is_a_setting_the_card_can_write():
    assert hands._setting("session", "ses_1") == ("executor_session", "ses_1")


async def test_recent_work_is_a_list_a_model_can_read():
    class Recent:
        async def recent(self):
            return [{"id": "ses_1", "title": "Change b to B",
                     "time": {"updated": 1_794_760_000_000}}]

    job = hand.Job(1, "що робилося", verb="sessions")
    text = await hands._verb(Recent(), job, None)

    assert text.startswith("Recent work, newest first:")
    assert "Change b to B (ses_1, " in text


async def test_a_plan_order_runs_the_turn_in_the_agent_that_only_looks():
    class Planned:
        plan = False

        async def run(self, request, desk, *, resume="", plan=False):
            self.plan = plan
            return "Ось план."

    agent = Planned()
    job = hand.Job(1, "перейменувати теку", verb="plan")
    assert await hands._verb(agent, job, None) == "Ось план."
    assert agent.plan is True


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
    agent = hands._agent(settings, None)

    assert agent is not None
    assert seen["known"].name == "opencode"
    assert seen["command"] == [str(shim), "serve"]
    assert seen["env"]["DEEPSEEK_API_KEY"] == "stored-value"
    assert seen["model"] == "deepseek/deepseek-chat"
    assert seen["config_patch"] == {
        "provider": {"deepseek": {"options": {"baseURL": "https://gateway.test"}}},
    }


def test_a_missing_binary_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(hands.runner, "_EXTRA_DIRS", ())
    settings = _settings(tmp_path, executor="opencode",
                        executor_binary="no-such-opencode")
    assert hands._agent(settings, None) is None
    assert "no-such-opencode" in capsys.readouterr().err


def test_an_unknown_runner_type_is_refused(tmp_path, capsys):
    settings = _settings(tmp_path, executor="hosted")
    assert hands._agent(settings, None) is None
    assert "hosted" in capsys.readouterr().err


def _named(ui: list[dict]) -> dict[str, dict]:
    return {c["name"]: c for c in ui}


def test_the_persons_own_additions_are_merged_but_not_the_permissions(tmp_path):
    machine = tmp_path / "machine"
    config = machine / "opencode"
    config.mkdir(parents=True)
    (config / "extra.json").write_text(json.dumps({
        "plugin": ["oh-my@1"],
        "provider": {"deepseek": {"options": {"baseURL": "https://x"}}},
        "permission": {"*": "allow"},
    }), encoding="utf-8")

    env = agents._opencode_env(machine, mode="careful")
    written = json.loads(Path(env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))

    assert written["plugin"] == ["oh-my@1"]
    assert written["provider"]["deepseek"]["options"]["baseURL"] == "https://x"
    # The floor stays ours whatever the person's file says.
    assert written["permission"]["*"] == "ask"
    assert written["permission"]["edit"] == "ask"


def test_the_agent_signs_in_to_its_own_home(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_run(argv, env=None, **kw):
        seen["argv"], seen["env"] = argv, env
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(hands.runner, "resolve", lambda binary: "/bin/true")
    monkeypatch.setattr(hands.subprocess, "run", fake_run)

    assert hands._agent_login() == 0
    assert seen["argv"] == ["/bin/true", "auth", "login"]
    assert seen["env"]["HOME"] == str(hands.runner.agent_home() / "home")


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
    # One runner is a fact, not a choice: a label until a second one exists.
    assert ui["type"]["kind"] == "label"
    assert ui["type"]["value"] == "opencode"
    assert ui["found"]["value"] == str(shim)
    assert [o["value"] for o in ui["model"]["options"]] == [
        "", "deepseek/deepseek-chat"]
    assert ui["login"]["value"] == "deepseek"
    assert "auth_env" not in ui


def test_a_key_sign_in_shows_the_key_and_whether_it_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "models", lambda kind, binary: [])
    keys.store("ANTHROPIC_API_KEY", "stored-value")

    ui = _named(hands._settings_ui(_settings(
        tmp_path, executor="opencode", executor_auth="key",
        executor_auth_env="ANTHROPIC_API_KEY")))

    assert ui["auth_env"]["value"] == "ANTHROPIC_API_KEY"
    assert ui["key"]["value"] == "present (in Keychain)"
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
    assert hands._setting("type", "opencode") == ("executor", "opencode")
    assert hands._setting("model", " a/b ") == ("executor_model", "a/b")
    assert hands._setting("auth_env", "DEEPSEEK_API_KEY") == (
        "executor_auth_env", "DEEPSEEK_API_KEY")
    assert hands._setting("type", "hosted") is None
    assert hands._setting("type", "claude") is None  # not a runner any more
    assert hands._setting("auth", "password") is None
    assert hands._setting("auth_env", "HOME") is None
    assert hands._setting("taking", "yes") is None
    assert hands._setting("binary", 3) is None
    assert hands._setting("mode", "auto") == ("executor_mode", "auto")
    assert hands._setting("mode", "reckless") is None


def test_taking_work_survives_a_restart(tmp_path):
    settings = _settings(tmp_path)
    assert hands._taking(settings) is True

    assert hands._take_work(settings, False) is False

    assert hands._taking(Settings(path=tmp_path / "config.toml")) is False


def test_the_check_is_one_line_per_need(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "health", lambda *a: {"ok": False, "checks": [
        {"name": "binary", "ok": True, "detail": "/bin/opencode"},
        {"name": "auth", "ok": False, "detail": "no sign-in"}]})

    assert hands._checks(_settings(tmp_path)) == (
        "✓ binary: /bin/opencode\n✗ auth: no sign-in")


def test_a_missing_key_says_how_to_add_it_on_this_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(hands.runner, "models", lambda kind, binary: [])
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    ui = _named(hands._settings_ui(_settings(
        tmp_path, executor="opencode", executor_auth="key",
        executor_auth_env="GROQ_API_KEY")))

    assert "heare-cli-hands key GROQ_API_KEY" in ui["key"]["value"]


def test_a_key_is_kept_on_the_hands_machine(monkeypatch, capsys):
    monkeypatch.setattr(hands.getpass, "getpass", lambda prompt: " typed ")
    assert hands._store_key("ZAI_API_KEY") == 0
    assert keys.get("ZAI_API_KEY") == "typed"
    assert "typed" not in capsys.readouterr().out


def test_an_unknown_key_or_nothing_typed_is_not_kept(monkeypatch):
    monkeypatch.setattr(hands.getpass, "getpass", lambda prompt: "")
    assert hands._store_key("HOME") == 1
    assert hands._store_key("ZAI_API_KEY") == 1


def test_hands_sweep_away_sockets_of_hands_that_are_gone(tmp_path):
    import os

    from heare.cli_hands import _sweep

    gone = tmp_path / "hands-999999.sock"
    mine = tmp_path / f"hands-{os.getpid()}.sock"
    odd = tmp_path / "hands-x.sock"
    for path in (gone, mine, odd):
        path.write_text("")

    _sweep(tmp_path)

    assert not gone.exists()
    assert mine.exists() and odd.exists()
