"""Tests for src/main.py CLI commands."""
from __future__ import annotations

import asyncio
import os
from unittest.mock import Mock


def test_set_passphrase_writes_to_config(tmp_path, monkeypatch) -> None:
    """set-passphrase command writes confirmation_passphrase to config.toml."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    from src.main import _cmd_set_wake_word

    args = Mock(word="авторизую")
    result = _cmd_set_wake_word(args)

    assert result == 0
    config_file = tmp_path / "config.toml"
    assert config_file.exists()
    content = config_file.read_text()
    assert 'confirmation_passphrase = "авторизую"' in content

    # Verify onboarding flag is created
    onboarding_flag = tmp_path / ".onboarded"
    assert onboarding_flag.exists()


def test_set_passphrase_rejects_empty(tmp_path, monkeypatch) -> None:
    """set-passphrase command rejects empty passphrase."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    from src.main import _cmd_set_wake_word

    args = Mock(word="  ")
    result = _cmd_set_wake_word(args)

    assert result == 1  # error exit code


def test_set_passphrase_updates_existing_config(tmp_path, monkeypatch) -> None:
    """set-passphrase command updates existing confirmation_passphrase line."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    config_file = tmp_path / "config.toml"
    config_file.write_text('confirmation_passphrase = "old"\nmode = "ambient"\n')

    from src.main import _cmd_set_wake_word

    args = Mock(word="newpass")
    result = _cmd_set_wake_word(args)

    assert result == 0
    content = config_file.read_text()
    assert 'confirmation_passphrase = "newpass"' in content
    assert '"old"' not in content
    # Other lines preserved
    assert 'mode = "ambient"' in content


def test_spoken_action_summary_passes_through_short_text() -> None:
    """US-A-02: short summary is returned verbatim after scrub."""
    from src.main import _spoken_action_summary

    out = _spoken_action_summary("hi", lambda x: x)
    assert out == "hi"


def test_spoken_action_summary_empty_uses_fallback() -> None:
    """US-A-02: empty summary yields the Ukrainian acknowledgement fallback."""
    from src.main import _ACTION_SUCCESS_FALLBACK, _spoken_action_summary

    assert _spoken_action_summary("", lambda x: x) == _ACTION_SUCCESS_FALLBACK
    assert (
        _spoken_action_summary("   \n\n  \t", lambda x: x)
        == _ACTION_SUCCESS_FALLBACK
    )


def test_spoken_action_summary_clips_to_120_chars() -> None:
    """US-A-02: the spoken phrase is bounded so TTS stays snappy."""
    from src.main import _spoken_action_summary

    long = "a" * 500
    spoken = _spoken_action_summary(long, lambda x: x)
    assert len(spoken) == 120


def test_spoken_action_summary_applies_scrubber() -> None:
    """US-A-02: the generator scrubber prevents tool-name leak."""
    from src.generator import _scrub_tts_text
    from src.main import _ACTION_SUCCESS_FALLBACK, _spoken_action_summary

    out = _spoken_action_summary("bash", _scrub_tts_text)
    assert out == _ACTION_SUCCESS_FALLBACK  # scrubbed to empty → fallback


def test_spoken_action_summary_collapses_whitespace() -> None:
    """US-A-02: newlines and repeated whitespace flatten into one line."""
    from src.main import _spoken_action_summary

    out = _spoken_action_summary("line1\n\nline2\nline3", lambda x: x)
    assert out == "line1 line2 line3"


def test_action_error_hint_timeout_uses_dedicated_phrase() -> None:
    """US-A-03: TimeoutError → Ukrainian 'дія не встигла' hint."""
    from src.main import _ACTION_TIMEOUT_HINT, _action_error_hint

    assert _action_error_hint(asyncio.TimeoutError()) == _ACTION_TIMEOUT_HINT


def test_action_error_hint_other_exceptions_name_the_class() -> None:
    """US-A-03: non-timeout errors include the exception class name."""
    from src.main import _action_error_hint

    hint = _action_error_hint(RuntimeError("boom"))
    assert "RuntimeError" in hint
    assert hint.startswith("Не вдалося")


def test_resolve_spoken_picks_language_from_dict() -> None:
    """VFA-05 AC(a): dict with en/uk/ru — intent.language='uk' returns Ukrainian."""
    from src.actions import Intent
    from src.main import _resolve_spoken

    result = {"spoken": {"en": "Done.", "uk": "Готово.", "ru": "Готово."}, "success": True}
    intent = Intent(id=1, tool="bash", args="x", raw={}, language="uk")
    assert _resolve_spoken(result, intent, fallback_summary="fallback") == "Готово."


def test_resolve_spoken_falls_back_to_english_for_unknown_language() -> None:
    """VFA-05 AC(b): dict with only 'en' — unknown intent.language falls back to English."""
    from src.actions import Intent
    from src.main import _resolve_spoken

    result = {"spoken": {"en": "Done."}, "success": True}
    intent = Intent(id=1, tool="bash", args="x", raw={}, language="ja")
    assert _resolve_spoken(result, intent, fallback_summary="fallback") == "Done."


def test_resolve_spoken_str_form_used_as_is() -> None:
    """VFA-05 AC(c): spoken is a plain str — returned as-is regardless of language."""
    from src.actions import Intent
    from src.main import _resolve_spoken

    result = {"spoken": "File saved.", "success": True}
    intent = Intent(id=1, tool="write", args="x", raw={}, language="uk")
    assert _resolve_spoken(result, intent, fallback_summary="fallback") == "File saved."


def test_resolve_spoken_none_falls_back_to_generic_done_per_language() -> None:
    """VFA-05 AC(d) / VFA-06 AC(a): no spoken + success=True → generic Done per language."""
    from src.actions import Intent
    from src.main import _resolve_spoken

    result = {"success": True}
    intent = Intent(id=1, tool="bash", args="x", raw={}, language="uk")
    assert _resolve_spoken(result, intent, fallback_summary="fallback") == "Готово."


def test_resolve_spoken_generic_failure_per_language() -> None:
    """VFA-06 AC(b): no spoken + success=False → generic failure sentence per language."""
    from src.actions import Intent
    from src.main import _resolve_spoken

    result = {"success": False}
    intent = Intent(id=1, tool="bash", args="x", raw={}, language="ru")
    assert _resolve_spoken(result, intent, fallback_summary="fallback") == "Не удалось выполнить."


def test_resolve_spoken_falls_back_to_summary_when_provided() -> None:
    """VFA-06 AC(c): when spoken is None and success key is absent, return fallback_summary.

    This is the explicit opt-in path for direct-tool short output that the
    caller has already computed as a clean spoken phrase.
    """
    from src.actions import Intent
    from src.main import _resolve_spoken

    # No 'success' key → neither Done nor failure branch fires → fallback used.
    result = {}
    intent = Intent(id=1, tool="bash", args="x", raw={}, language="en")
    assert _resolve_spoken(result, intent, fallback_summary="my summary") == "my summary"


def test_multiple_daemon_instances_prevented(tmp_path, monkeypatch) -> None:
    """Starting a second daemon instance fails gracefully when first is running."""
    monkeypatch.setattr("src.config.HEARE_HOME", tmp_path)

    # Create a fake PID file with current process ID
    pid_file = tmp_path / "heare.pid"
    pid_file.write_text(str(os.getpid()))

    from src.main import _cmd_start

    args = Mock()
    result = asyncio.run(_cmd_start(args))

    # Should fail because "daemon" (current process) is already running
    assert result == 1

    # Verify PID file still exists
    assert pid_file.exists()
