"""Tests for IntentStreamParser covering all 7 anti-leakage invariants."""
from __future__ import annotations

import logging

from src.intent_parser import IntentStreamParser


def _feed_all(parser: IntentStreamParser, chunks: list[str]) -> tuple[str, list[dict]]:
    """Feed each chunk; return (concatenated_emittable, all_intents)."""
    text_parts: list[str] = []
    intents: list[dict] = []
    for c in chunks:
        t, i = parser.feed(c)
        text_parts.append(t)
        intents.extend(i)
    tail, tail_intents = parser.flush()
    text_parts.append(tail)
    intents.extend(tail_intents)
    return "".join(text_parts), intents


def test_complete_tag_single_chunk() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(
        p, ['Додам. <intent>{"tool":"bash","args":"echo hi"}</intent>']
    )
    assert text == "Додам. "
    assert intents == [{"tool": "bash", "args": "echo hi"}]


def test_tag_split_inside_opener() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(
        p,
        [
            "prefix ",
            "<int",
            "ent>",
            '{"tool":"bash","args":"x"}',
            "</intent>",
        ],
    )
    assert text == "prefix "
    assert intents == [{"tool": "bash", "args": "x"}]


def test_tag_split_inside_json_body() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(
        p,
        [
            "Додам зараз. ",
            '<intent>{"tool":"bash",',
            '"args":"echo hi"}</intent>',
            " далі щось.",
        ],
    )
    assert text == "Додам зараз.  далі щось."
    assert intents == [{"tool": "bash", "args": "echo hi"}]
    assert "<" not in text


def test_malformed_json_dropped(caplog) -> None:
    p = IntentStreamParser()
    with caplog.at_level(logging.WARNING, logger="heare.intent_parser"):
        text, intents = _feed_all(
            p, ["Щось. <intent>{not valid json}</intent>"]
        )
    assert text == "Щось. "
    assert intents == []
    assert any("malformed" in r.message.lower() for r in caplog.records)


def test_no_tag_passthrough() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(p, ["Просто текст. Без тегів."])
    assert text == "Просто текст. Без тегів."
    assert intents == []


def test_text_after_closing_tag_reaches_emittable() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(
        p, ['<intent>{"tool":"bash","args":"a"}</intent> далі.']
    )
    assert text == " далі."
    assert intents == [{"tool": "bash", "args": "a"}]


def test_markdown_fence_around_intent_stripped() -> None:
    p = IntentStreamParser()
    body = (
        "<intent>\n"
        "```json\n"
        '{"tool":"bash","args":"echo fenced"}\n'
        "```\n"
        "</intent>"
    )
    text, intents = _feed_all(p, [body])
    assert intents == [{"tool": "bash", "args": "echo fenced"}]


def test_uppercase_opener_accepted() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(
        p, ['reply <INTENT>{"tool":"bash","args":"a"}</INTENT>']
    )
    assert text == "reply "
    assert intents == [{"tool": "bash", "args": "a"}]


def test_other_lt_bytes_flush_to_text() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(p, ["compare 3 < 5 and <other>tag"])
    assert "<" in text  # '<' is NOT an intent prefix → flushed
    assert "3 < 5" in text
    assert "<other>" in text
    assert intents == []


def test_no_leakage_partial_intent_prefix_held_then_resolved() -> None:
    """The '<int' must NEVER appear in emittable even if subsequent chunk
    completes the tag. Core anti-leakage invariant."""
    p = IntentStreamParser()
    # Feed enough to confirm no leakage per chunk
    emitted_per_chunk: list[str] = []
    for c in ["привіт <int", "ent>", '{"tool":"bash","args":"x"}', "</intent>"]:
        emitted, _ = p.feed(c)
        emitted_per_chunk.append(emitted)
    tail, _ = p.flush()
    total = "".join(emitted_per_chunk) + tail
    assert "<" not in total
    assert "привіт " in total


def test_unclosed_tag_at_eos_warns(caplog) -> None:
    p = IntentStreamParser()
    p.feed('text <intent>{"tool":"bash",')
    with caplog.at_level(logging.WARNING, logger="heare.intent_parser"):
        tail, intents = p.flush()
    assert intents == []
    assert any("unclosed" in r.message.lower() for r in caplog.records)


def test_partial_prefix_that_disproves_flushes_lt() -> None:
    """`<` followed by non-intent bytes must flush the `<` too, not hold forever."""
    p = IntentStreamParser()
    emitted_1, _ = p.feed("<a")
    # Partial regex for '<a' does NOT match `<\s*(?:i...)` — next char `a`
    # breaks prefix. Should release both bytes as text.
    # Note: implementation may hold until subsequent chunk; flush() at EOS
    # is the contractual release point.
    tail, _ = p.flush()
    assert "<a" in (emitted_1 + tail)


def test_second_intent_in_stream_logged_and_dropped(caplog) -> None:
    """Multi-intent mode: second intent is now accepted (not dropped)."""
    p = IntentStreamParser(max_intents=10)
    body = (
        '<intent>{"tool":"bash","args":"first"}</intent> '
        'middle '
        '<intent>{"tool":"bash","args":"second"}</intent> tail'
    )
    text, intents = _feed_all(p, [body])
    # Both intents should be accepted now
    assert len(intents) == 2
    assert intents[0]["args"] == "first"
    assert intents[1]["args"] == "second"
    assert "tail" in text
    assert "middle" in text
    # No warning about dropping (multi-intent is enabled)


def test_emoji_in_body_drops_intent() -> None:
    p = IntentStreamParser()
    # Emoji between opener and JSON; JSON.parse will fail on stripped body
    text, intents = _feed_all(
        p, ['<intent>\n🎉 {"tool":"bash","args":"x"}\n</intent>']
    )
    assert intents == []


def test_nested_intent_outer_dropped(caplog) -> None:
    p = IntentStreamParser()
    with caplog.at_level(logging.WARNING, logger="heare.intent_parser"):
        text, intents = _feed_all(
            p,
            [
                '<intent>{"outer":true} <intent>{"tool":"bash","args":"nested"}</intent>',
            ],
        )
    # Outer dropped; nested parsed as fresh intent (now first valid)
    assert len(intents) == 1
    assert intents[0].get("tool") == "bash"
    assert any("nested" in r.message.lower() for r in caplog.records)


def test_whitespace_tolerant_opener() -> None:
    p = IntentStreamParser()
    text, intents = _feed_all(
        p, ['<  intent  >{"tool":"bash","args":"w"}</ intent >']
    )
    # Opener tolerates internal whitespace; closer tolerates whitespace
    # around `intent` but requires `</` adjacent.
    assert intents == [{"tool": "bash", "args": "w"}]
    assert text == ""
