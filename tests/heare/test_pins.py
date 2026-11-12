"""Pins: an anchor to a message, not a copy of it."""

from __future__ import annotations

from heare.api.pins import PinStore
from heare.api.sessions import Sessions


def _room(tmp_path):
    sessions = Sessions(tmp_path / "s")
    session = sessions.new()
    store = PinStore(tmp_path / "pins.json")
    store.bind_sessions(sessions)
    return sessions, session, store


def test_a_pin_points_at_a_message_and_dies_with_it(tmp_path):
    sessions, session, store = _room(tmp_path)
    message = sessions.add(session, "assistant", "привіт")
    pin = store.add(session, message["id"], by="person", title="Привіт")
    assert pin["message"] == message["id"] and pin["title"] == "Привіт"
    assert store.all()[0]["id"] == pin["id"]
    assert store.remove(pin["id"]) and store.all() == []
    store.add(session, message["id"])
    assert store.drop_session(session) == 1 and store.all() == []


def test_a_pin_is_idempotent_and_needs_a_message(tmp_path):
    sessions, session, store = _room(tmp_path)
    message = sessions.add(session, "assistant", "один")
    first = store.add(session, message["id"], by="person", title="Один")
    second = store.add(session, message["id"], by="person", title="Один!")
    assert first["id"] == second["id"] and first["order"] == second["order"]
    assert len(store.all()) == 1 and store.all()[0]["title"] == "Один!"
    try:
        store.add(session, f"{session}/99")
    except LookupError:
        pass
    else:
        raise AssertionError("a missing message must not pin")


def test_an_ask_cannot_be_pinned_and_the_dock_is_limited(tmp_path):
    sessions, session, store = _room(tmp_path)
    ask = sessions.add(session, "assistant", "пускати?", kind="ask")
    try:
        store.add(session, ask["id"], by="person")
    except ValueError:
        pass
    else:
        raise AssertionError("an ask must not pin")
    for i in range(13):
        sessions.add(session, "assistant", f"кадр {i}")
    for i in range(12):
        store.add(session, f"{session}/{i + 1}", by="person")
    try:
        store.add(session, f"{session}/13", by="person")
    except OverflowError:
        pass
    else:
        raise AssertionError("thirteen pins in one session must not fit")


def test_a_self_pin_binds_to_the_next_reply(tmp_path):
    sessions, session, store = _room(tmp_path)
    store.claim_self(session)
    assert store.all() == []
    message = sessions.add(session, "assistant", "ось таблиця")
    pin = store.bind_self(session, message["id"])
    assert pin is not None and store.all()[0]["message"] == message["id"]


def test_it_survives_a_restart_and_names_what_is_gone(tmp_path):
    sessions, session, store = _room(tmp_path)
    message = sessions.add(session, "assistant", "привіт")
    store.add(session, message["id"], by="person")
    again = PinStore(tmp_path / "pins.json")
    again.bind_sessions(sessions)
    assert len(again.all()) == 1
    sessions.delete(session)
    assert again.all()[0]["missing"] is True


def test_resolve_finds_latest_ids_and_words(tmp_path):
    sessions, session, store = _room(tmp_path)
    first = sessions.add(session, "assistant", "погода в Бурштині")
    second = sessions.add(session, "assistant", "таблиця що змінилось")
    assert store.resolve("latest") == second["id"]
    assert store.resolve(first["id"]) == first["id"]
    assert store.resolve("таблиця що змінилось") == second["id"]
    assert store.resolve("зовсім інше") is None
