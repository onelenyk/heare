"""The entry code carried to a phone: what Telegram is told, and when it is not."""

from __future__ import annotations

from heare import keys
from heare.api import notify
from heare.config import Settings


def _settings(tmp_path, **given):
    return Settings(path=tmp_path / "config.toml", **given)


def test_nothing_is_sent_until_a_chat_is_named(tmp_path):
    keys.store("TELEGRAM_BOT_TOKEN", "test-token")
    sent: list[dict[str, object]] = []

    assert notify.carry("ABCD2345", _settings(tmp_path),
                        send=lambda token, payload: sent.append(payload)) is False
    assert sent == []


def test_nothing_is_sent_without_a_bot(tmp_path):
    sent: list[dict[str, object]] = []
    settings = _settings(tmp_path, telegram_chat_id="-1003966928448")

    assert notify.carry("ABCD2345", settings,
                        send=lambda token, payload: sent.append(payload)) is False
    assert sent == []


def test_the_code_itself_reaches_the_chat(tmp_path):
    keys.store("TELEGRAM_BOT_TOKEN", "test-token")
    settings = _settings(tmp_path, telegram_chat_id="-1003966928448")
    sent: list[tuple[str, dict[str, object]]] = []

    assert notify.carry("ABCD2345", settings,
                        send=lambda token, payload: sent.append((token, payload)))

    token, payload = sent[0]
    assert token == "test-token"
    assert payload["chat_id"] == "-1003966928448"
    assert "ABCD2345" in str(payload["text"])
    assert payload["parse_mode"] == "HTML"
    assert "message_thread_id" not in payload


def test_a_topic_is_named_where_the_chat_has_one(tmp_path):
    keys.store("TELEGRAM_BOT_TOKEN", "test-token")
    settings = _settings(tmp_path, telegram_chat_id="-1003966928448",
                         telegram_thread_id="11034")
    sent: list[dict[str, object]] = []

    assert notify.carry("ABCD2345", settings,
                        send=lambda token, payload: sent.append(payload))

    assert sent[0]["message_thread_id"] == 11034


def test_a_broken_send_keeps_the_code_and_the_log_clean(tmp_path, caplog):
    keys.store("TELEGRAM_BOT_TOKEN", "test-token")
    settings = _settings(tmp_path, telegram_chat_id="-1003966928448")

    def broken(token: str, payload: dict[str, object]) -> None:
        raise OSError("no route to the phone")

    assert notify.carry("ABCD2345", settings, send=broken) is False
    assert "ABCD2345" not in caplog.text
    assert "test-token" not in caplog.text


def test_the_message_speaks_the_person_s_language(tmp_path):
    assert "ABCD2345" in notify.text("ABCD2345")
    assert "код входу" in notify.text("ABCD2345")
