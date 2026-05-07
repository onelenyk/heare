"""Tests for text_scrub — defends TTS against tool-name narration."""
from __future__ import annotations

from src.pipeline.stages.text_scrub import scrub_tts_text


class TestExistingPatterns:
    """Original AH2-02 / Phase 2.2 cases must still pass."""

    def test_drops_bash_completed_with_no_output(self) -> None:
        assert scrub_tts_text("(Bash completed with no output)") == ""

    def test_drops_standalone_bash_token(self) -> None:
        # Standalone "bash" gets stripped but in this context the surrounding
        # word "Running" doesn't disappear, so we end up with "Running ."
        result = scrub_tts_text("Running bash now.")
        assert "bash" not in result.lower()
        assert "Running" in result

    def test_keeps_bashful_word(self) -> None:
        out = scrub_tts_text("Don't be bashful about it.")
        assert "bashful" in out

    def test_drops_intent_json(self) -> None:
        out = scrub_tts_text('Reply: {"tool": "bash", "args": "ls"}')
        assert '"tool"' not in out
        assert '"args"' not in out


class TestToolNameOnlyDropped:
    """The reported regression: model emits a bare tool name as its
    entire reply (e.g. ``list_tools``) and TTS reads it aloud."""

    def test_bare_list_tools_becomes_empty(self) -> None:
        assert scrub_tts_text("list_tools") == ""

    def test_bare_list_capabilities_becomes_empty(self) -> None:
        assert scrub_tts_text("list_capabilities") == ""

    def test_bare_create_skill_becomes_empty(self) -> None:
        # Sanity: tool list comes from registry, so newly-added tools
        # like create_skill are caught automatically.
        assert scrub_tts_text("create_skill") == ""

    def test_bare_tool_name_with_period_dropped(self) -> None:
        assert scrub_tts_text("list_tools.") == ""

    def test_bare_tool_name_with_whitespace_dropped(self) -> None:
        assert scrub_tts_text("  list_tools  ") == ""

    def test_natural_sentence_with_tool_name_word_kept(self) -> None:
        # "I'll use my tools" is normal speech, not a tool-name-only reply.
        # The string contains "list_tools" as a token? It doesn't —
        # this guards against over-matching common English.
        out = scrub_tts_text("I have many tools available.")
        assert "I have many tools available." == out


class TestToolColonNarrationStripped:
    """Pattern observed in production: ``bash: <command>``, where the
    model wrote the call as text instead of invoking the function."""

    def test_bash_colon_command_dropped(self) -> None:
        out = scrub_tts_text("bash: system_profiler SPHardwareDataType")
        assert "system_profiler" not in out
        assert "bash" not in out.lower()

    def test_read_colon_path_dropped(self) -> None:
        out = scrub_tts_text("read: /etc/hosts")
        assert "/etc/hosts" not in out

    def test_tool_colon_inline_in_sentence_stops_at_punctuation(self) -> None:
        # "Sure. bash: ls. Done." — the bash:ls clause is dropped, the
        # surrounding sentences survive.
        out = scrub_tts_text("Sure. bash: ls. Done.")
        assert "Sure" in out
        assert "Done" in out
        assert "ls" not in out


class TestSafeForRealResponses:
    """Real Ukrainian/English responses must NOT be mangled."""

    def test_ukrainian_kept_verbatim(self) -> None:
        s = "Звичайно, зачитуй — я уважно слухаю."
        assert scrub_tts_text(s) == s

    def test_short_english_kept(self) -> None:
        s = "Got it."
        assert scrub_tts_text(s) == s
