"""The installer page: it applies each answer as it is given, and closes."""

from __future__ import annotations

import io
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
from heare.core import i18n


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


def _post(url: str, data: dict, headers: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(data).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
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
                         machine_name_file=tmp_path / "machine_name",
                         api_token_file=tmp_path / "api_token",
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
        "native-hands": False}

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
        asked.update(url=url, code=code, kind=kind, name=name)
        return {"pid": "machine@MBP-M4--Nazar.lan", "token": "machine-token"}

    monkeypatch.setattr(setup, "pair", paired)
    with pytest.raises(urllib.error.HTTPError) as short:
        _post(base + "/role", {"role": "body", "url": "https://brain.example", "code": "123"})
    assert short.value.code == 400

    status, state = _post(base + "/role", {"role": "body", "url": "https://brain.example/",
                                           "code": "ABCD1234"})
    assert status == 200 and state["role"] == "body" and state["url"] == "https://brain.example"
    assert asked == {"url": "https://brain.example", "code": "ABCD1234", "kind": "machine",
                     "name": setup.socket.gethostname()}, \
        "a machine nobody named is called by its host, as before"
    assert (tmp_path / "machine_token").read_text() == "machine-token"
    assert (tmp_path / "machine_name").read_text() == "MBP-M4--Nazar.lan", \
        "the name the brain knows it by is kept: its parts key on it"


def test_a_machine_may_be_paired_under_a_name_of_its_own(form, tmp_path, monkeypatch):
    """Two computers may share a host name; the brain knows them by name."""
    base, config, _ = form
    asked = {}

    def paired(url, code, kind, name):
        asked.update(name=name)
        return {"pid": f"machine@{name}", "token": "machine-token"}

    monkeypatch.setattr(setup, "pair", paired)
    status, state = _post(base + "/role", {
        "role": "body", "url": "https://brain.example", "code": "ABCD1234",
        "name": " kitchen "})
    assert status == 200 and asked["name"] == "kitchen"
    assert (tmp_path / "machine_name").read_text() == "kitchen"


def test_a_taken_name_says_what_to_do_and_keeps_the_key(form, tmp_path, monkeypatch):
    base, config, _ = form

    def taken(url, code, kind, name):
        raise urllib.error.HTTPError(url, 409, "Conflict", {}, io.BytesIO(b"taken"))

    monkeypatch.setattr(setup, "pair", taken)
    with pytest.raises(urllib.error.HTTPError) as refused:
        _post(base + "/role", {"role": "body", "url": "https://brain.example",
                               "code": "ABCD1234", "name": "mac"})
    assert refused.value.code == 400
    answer = json.loads(refused.value.read().decode())
    assert answer["errors"] == [i18n._(
        "a machine with this name is already connected — forget it on the brain's page, "
        "or give this one another name")]
    assert not (tmp_path / "machine_token").exists()


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


def test_a_rebound_host_cannot_drive_the_form(form):
    base, config, _ = form
    host = f"evil.test:{base.rsplit(':', 1)[1]}"

    with pytest.raises(urllib.error.HTTPError) as page:
        urllib.request.urlopen(
            urllib.request.Request(base + "/", headers={"Host": host}), timeout=2)
    assert page.value.code == 403

    with pytest.raises(urllib.error.HTTPError) as role:
        _post(base + "/role", {"role": "all"},
              headers={"Host": host, "Origin": f"http://{host}"})
    assert role.value.code == 403
    assert not Settings(config).role


def test_a_foreign_origin_cannot_save_a_key(form, monkeypatch):
    base, _, _ = form
    monkeypatch.setattr(setup.machine, "check_key", lambda name, value: (True, "живий"))
    with pytest.raises(urllib.error.HTTPError) as refused:
        _post(base + "/key", {"name": "GROQ_API_KEY", "value": "stolen"},
              headers={"Origin": "https://evil.example"})
    assert refused.value.code == 403
    assert keys.get("GROQ_API_KEY") is None


def test_the_installer_page_still_works_from_loopback(form, monkeypatch):
    base, _, _ = form
    with urllib.request.urlopen(
            urllib.request.Request(base + "/", headers={"Origin": base}), timeout=2) as answer:
        assert answer.status == 200

    monkeypatch.setattr(setup.machine, "check_key", lambda name, value: (True, "живий"))
    status, state = _post(base + "/role", {"role": "all"}, headers={"Origin": base})
    assert status == 200 and state["role"] == "all"

    status, state = _post(base + "/done", {})
    assert status == 200 and state["done"] is True


def test_switching_from_body_to_all_forgets_the_machine_token(form, tmp_path):
    base, config, _ = form
    (tmp_path / "api_token").write_text("master", encoding="utf-8")
    (tmp_path / "machine_token").write_text("machine", encoding="utf-8")
    Settings(config).set("url", "https://brain.example")
    Settings(config).set("role", "body")

    status, state = _post(base + "/role", {"role": "all"})

    assert status == 200 and state["role"] == "all" and state["url"] == ""
    assert not (tmp_path / "machine_token").exists()
    settings = Settings(config, machine_token_file=tmp_path / "machine_token",
                        api_token_file=tmp_path / "api_token")
    assert settings.secret == "master"


def test_a_taken_setup_port_says_which(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        with pytest.raises(OSError) as taken:
            setup.serve(port=port)

    assert str(port) in str(taken.value)
    assert setup.state()["state"] == "taken"
    assert setup.state()["port"] == port
