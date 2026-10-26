"""The installer page: it applies each answer as it is given, and closes."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from heare import keys
from heare import setup
from heare.config import Settings


def test_want_setup_until_the_first_run_is_done(tmp_path):
    settings = Settings(tmp_path / "config.toml")
    assert setup.want_setup(settings)
    settings.set("setup_done", True)
    assert not setup.want_setup(settings)
    settings.set("setup_done", False)
    assert setup.want_setup(settings)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(url: str) -> None:
    for _ in range(100):
        try:
            with urllib.request.urlopen(url, timeout=1) as answer:
                if answer.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.02)
    raise AssertionError("the form did not start")


def _post(url: str, data: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as answer:
        return answer.status, json.loads(answer.read().decode())



@pytest.fixture
def form(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARE_NO_BROWSER", "1")
    config = tmp_path / "config.toml"
    machine = tmp_path / "machine_token"
    monkeypatch.setattr(
        setup, "Settings",
        lambda: Settings(config, machine_token_file=machine,
                         parts_dir=tmp_path / "parts"),
    )
    port = _free_port()
    thread = threading.Thread(target=setup.serve, kwargs={"port": port}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    _wait_for(base + "/")
    yield base, config, thread
    try:
        _post(base + "/close", {})
    except OSError:
        pass
    thread.join(timeout=5)


def test_the_form_no_longer_runs_an_installer(form):
    base, _, _ = form
    with pytest.raises(urllib.error.HTTPError) as gone:
        _post(base + "/setup", {"brain_here": True, "parts": []})
    assert gone.value.code == 404


def test_the_form_closes_when_asked(form):
    base, _, thread = form
    status, result = _post(base + "/close", {})
    assert status == 200 and result["ok"] is True
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_the_form_reports_where_each_key_comes_from(form, monkeypatch):
    base, _, _ = form
    monkeypatch.setenv("GROQ_API_KEY", "test-value")
    with urllib.request.urlopen(base + "/keys", timeout=2) as answer:
        assert answer.status == 200
        report = json.loads(answer.read().decode())
    assert set(report) == set(keys.NAMES)
    assert report["GROQ_API_KEY"] == {"source": "env", "env": True, "stored": False}
    assert report["DEEPSEEK_API_KEY"] == {"source": None, "env": False, "stored": False}


def test_the_form_adopts_a_key_from_the_environment(form, monkeypatch):
    base, _, _ = form
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-value")
    status, result = _post(base + "/keys", {"name": "DEEPSEEK_API_KEY"})
    assert status == 200
    assert result == {"saved": "DEEPSEEK_API_KEY"}
    with urllib.request.urlopen(base + "/keys", timeout=2) as answer:
        report = json.loads(answer.read().decode())
    assert report["DEEPSEEK_API_KEY"] == {"source": "env", "env": True, "stored": True}


def test_the_form_refuses_to_adopt_what_is_not_there(form):
    base, _, _ = form
    with pytest.raises(urllib.error.HTTPError) as unknown:
        _post(base + "/keys", {"name": "PATH"})
    assert unknown.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as missing:
        _post(base + "/keys", {"name": "GROQ_API_KEY"})
    assert missing.value.code == 400
    assert json.loads(missing.value.read().decode())["errors"]


def test_choosing_the_deepseek_key_makes_deepseek_the_provider(form, monkeypatch):
    base, config, _ = form
    monkeypatch.setattr(setup.machine, "check_key", lambda name, value: (True, "живий"))
    _, answer = _post(base + "/key", {"name": "DEEPSEEK_API_KEY", "value": "test-deepseek"})
    assert answer["ok"] is True
    assert keys.get("DEEPSEEK_API_KEY") == "test-deepseek"
    assert Settings(config).view()["llm_provider"] == "deepseek"


def test_the_form_turns_a_part_on_and_off_with_no_install(form, tmp_path):
    base, _, _ = form
    with urllib.request.urlopen(base + "/machine", timeout=2) as answer:
        before = json.loads(answer.read().decode())
    assert {p["name"]: p["on"] for p in before["parts"]} == {
        "voice": False, "eyes": False, "sources": False, "hands": False,
        "native": False}

    status, after = _post(base + "/part", {"name": "voice", "on": True})
    assert status == 200
    assert next(p for p in after["parts"] if p["name"] == "voice")["on"] is True
    assert (tmp_path / "parts" / "voice" / "heare.json").exists()

    _post(base + "/part", {"name": "voice", "on": False})
    assert not (tmp_path / "parts" / "voice").exists()


def test_the_form_keeps_a_key_only_if_its_provider_takes_it(form, monkeypatch):
    base, _, _ = form

    def check(name, value):
        return (True, "живий") if value == "good" else (False, "ключ не підходить")

    monkeypatch.setattr(setup.machine, "check_key", check)

    status, bad = _post(base + "/key", {"name": "GROQ_API_KEY", "value": "bad"})
    assert status == 200 and bad["ok"] is False and bad["said"] == "ключ не підходить"
    assert keys.get("GROQ_API_KEY") is None

    _, good = _post(base + "/key", {"name": "GROQ_API_KEY", "value": "good"})
    assert good["ok"] is True and keys.get("GROQ_API_KEY") == "good"


def test_the_form_is_the_installer_page(form):
    base, _, _ = form
    with urllib.request.urlopen(base + "/", timeout=2) as answer:
        page = answer.read().decode()
    assert 'fetch("machine")' in page and "Цей комп'ютер — це…" in page


def test_a_body_is_paired_with_the_brain_it_names(form, tmp_path, monkeypatch):
    base, config, _ = form
    asked = {}

    def paired(url, code, kind, name):
        asked.update(url=url, code=code, kind=kind)
        return {"token": "machine-token"}

    monkeypatch.setattr(setup, "pair", paired)
    with pytest.raises(urllib.error.HTTPError) as short:
        _post(base + "/role", {"role": "body", "url": "https://brain.example", "code": "123"})
    assert short.value.code == 400

    status, state = _post(base + "/role", {"role": "body", "url": "https://brain.example/",
                                           "code": "ABCD1234"})
    assert status == 200 and state["role"] == "body" and state["url"] == "https://brain.example"
    assert asked == {"url": "https://brain.example", "code": "ABCD1234", "kind": "machine"}
    assert (tmp_path / "machine_token").read_text() == "machine-token"


def test_the_brain_here_forgets_a_brain_elsewhere(form):
    base, config, _ = form
    Settings(config).set("url", "https://elsewhere.example")
    _, state = _post(base + "/role", {"role": "all"})
    assert state["role"] == "all" and state["url"] == ""


def test_finishing_ends_the_first_run(form):
    base, config, _ = form
    _, state = _post(base + "/done", {})
    assert state["done"] is True
    assert Settings(config).setup_done is True


def test_a_first_run_starts_with_every_part_this_machine_can_have(form, tmp_path, monkeypatch):
    base, config, _ = form
    monkeypatch.setattr(setup.machine, "why_not", lambda name, bin_dir=None: None)
    monkeypatch.setattr(setup.machine, "command", lambda name, bin_dir=None: [f"heare-{name}"])
    _, state = _post(base + "/role", {"role": "all"})
    assert all(p["on"] for p in state["parts"])

    # Once set up, choosing again changes nothing the person turned off.
    _post(base + "/part", {"name": "eyes", "on": False})
    Settings(config).set("setup_done", True)
    _, again = _post(base + "/role", {"role": "all"})
    assert next(p for p in again["parts"] if p["name"] == "eyes")["on"] is False
