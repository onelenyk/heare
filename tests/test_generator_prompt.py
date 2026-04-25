"""Golden verification of prompts/generator.txt substitution shape."""
from __future__ import annotations

from pathlib import Path

from src.context import _safe_substitute


TEMPLATE_PATH = Path(__file__).parent.parent / "prompts" / "generator.txt"


def _load() -> str:
    return TEMPLATE_PATH.read_text()


def test_template_exists_and_nonempty() -> None:
    raw = _load()
    assert raw.strip(), "generator.txt is empty"


def test_template_has_exactly_expected_placeholders() -> None:
    raw = _load()
    import re

    found = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", raw))
    assert found == {
        "time",
        "timezone",
        "persona",
        "recent_transcripts",
        "transcript",
        "conversation_summary",
        "active_topics",
        "entities",
        "recent_turns",
        "recent_actions",
        "mcp_servers",
        "user_language",
    }


def test_template_contains_phase2_placeholders() -> None:
    raw = _load()
    for required in (
        "{conversation_summary}",
        "{active_topics}",
        "{entities}",
        "{recent_turns}",
        "{recent_actions}",
    ):
        assert required in raw, f"missing Phase-2.2 placeholder {required!r}"


def test_template_requires_user_language_response() -> None:
    raw = _load()
    assert "{user_language}" in raw
    assert "Always respond in {user_language}" in raw


def test_template_reply_text_forbids_json() -> None:
    raw = _load()
    lowered = raw.lower()
    assert "no json" in lowered or "never output json" in lowered


def test_template_has_intents_section() -> None:
    raw = _load()
    assert "INTENTS" in raw
    assert "<intent>" in raw
    assert '"tool"' in raw
    assert '"args"' in raw


def test_template_forbids_fenced_intent() -> None:
    raw = _load()
    assert "fences" in raw.lower() or "```" in raw


def test_template_intent_schema_lists_all_six_tools() -> None:
    raw = _load()
    for tool in ("bash", "read", "write", "edit", "web_fetch", "web_search"):
        assert f'"{tool}"' in raw, f"missing tool {tool!r} in schema"
    assert "mcp__" in raw, "missing MCP tools format documentation"


def test_template_has_positive_and_negative_intent_examples() -> None:
    raw = _load()
    positive_count = raw.count('<intent>{"tool":"bash"')
    assert positive_count >= 3, (
        f"expected >=3 positive bash intent examples, found {positive_count}"
    )
    assert "gmail.com" in raw, "expected the gmail.com negative example"
    assert "Хочу відкрити" in raw, "expected the 'Хочу відкрити щось' negative example"


def test_template_has_positive_example_per_non_bash_tool() -> None:
    raw = _load()
    for tool in ("read", "write", "edit", "web_fetch", "web_search"):
        marker = f'<intent>{{"tool":"{tool}"'
        assert marker in raw, (
            f"missing positive example for tool {tool!r} (looked for {marker!r})"
        )


def test_template_has_if_unsure_dont_emit_rule() -> None:
    raw = _load()
    lowered = raw.lower()
    assert "if unsure" in lowered or "do not add an intent" in lowered


def test_template_lists_forbidden_commands() -> None:
    raw = _load()
    assert "rm -rf" in raw, "expected 'rm -rf' in the forbidden commands section"
    assert "sudo" in raw, "expected 'sudo' in the forbidden commands section"


def test_template_lists_trigger_verbs_multilingual() -> None:
    """English and Ukrainian trigger verbs must both appear in the INTENTS section."""
    raw = _load()
    for verb in ("run", "open", "execute", "close", "create", "find"):
        assert verb in raw, f"expected English trigger verb {verb!r}"
    for verb in ("запусти", "відкрий", "виконай", "закрий", "створи", "знайди"):
        assert verb in raw, f"expected Ukrainian trigger verb {verb!r}"


def test_template_enforces_one_sentence_reply() -> None:
    raw = _load()
    assert "ONE sentence" in raw
    assert "12 words" in raw


def test_template_bans_filler() -> None:
    raw = _load()
    lowered = raw.lower()
    filler_names = ("thanks", "apologies", "alternatives", "promises")
    hits = sum(1 for name in filler_names if name in lowered)
    assert hits >= 3, (
        f"expected >=3 filler categories named, found {hits} in prompt"
    )


def test_template_caps_pre_intent_wording() -> None:
    raw = _load()
    lowered = raw.lower()
    assert "5 words" in lowered or "up to 5" in lowered or "short" in lowered


def test_template_forbids_permission_asks_after_command() -> None:
    raw = _load()
    lowered = raw.lower()
    assert "do not ask permission" in lowered or "don't ask permission" in lowered


def test_template_has_use_prior_search_rule() -> None:
    """Generator must be instructed to read prior search results before re-searching."""
    raw = _load()
    lowered = raw.lower()
    assert "recent_actions" in raw, "rule must mention recent_actions"
    forbid_phrases = ("do not", "don't", "avoid")
    assert any(p in lowered for p in forbid_phrases), (
        "rule must explicitly forbid re-searching (do not/don't/avoid)"
    )
    assert "web_search" in raw or "search" in lowered


def test_template_has_prior_search_followup_example() -> None:
    """At least one example must show a follow-up answered from prior search
    content WITHOUT a new web_search intent."""
    raw = _load()
    # Locate the recipe follow-up example block.
    needle = "give me the recipe"
    assert needle in raw, "expected an English recipe follow-up example"
    block_start = raw.index(needle)
    # Bound the block at the next blank line gap or 600 chars.
    block = raw[block_start:block_start + 600]
    # The follow-up Response must NOT contain a web_search intent for the
    # same topic — that is the whole point of the rule.
    assert '"tool":"web_search"' not in block, (
        "follow-up example must answer from prior search content, not re-search"
    )


def test_template_has_numbered_results_addressing_rule() -> None:
    """CCS-02: prompt teaches the model to address numbered web_search
    items by ordinal ('the second one') and qualifier ('the vegan one'),
    referencing recent_actions."""
    raw = _load()
    assert "the second one" in raw, "ordinal example 'the second one' missing"
    assert "the vegan one" in raw, "qualifier example 'the vegan one' missing"
    assert "recent_actions" in raw, "rule must mention recent_actions"
    # uk/ru ordinal/qualifier vocabulary present
    assert "другий" in raw or "веганський" in raw or "второй" in raw or "веганский" in raw, (
        "expected at least one uk/ru ordinal or qualifier term"
    )


def test_template_has_ordinal_or_qualifier_example() -> None:
    """CCS-02: at least one example block shows ordinal OR qualifier
    reference resulting in an answer that does NOT emit a new web_search
    intent for the same topic."""
    raw = _load()
    # Find the addressing-numbered-results section
    needles = ("the second one", "веганський")
    found = [n for n in needles if n in raw]
    assert found, f"expected one of {needles!r} in prompt"

    # For each found needle, check the surrounding example block (next 600
    # chars) does NOT emit a web_search intent.
    for needle in found:
        idx = raw.index(needle)
        block = raw[idx:idx + 600]
        assert '"tool":"web_search"' not in block, (
            f"addressing example near {needle!r} must not re-search; got: {block!r}"
        )


def test_template_has_refinement_section() -> None:
    """CCS-04: prompt contains a REFINEMENT section header (case-insensitive)."""
    raw = _load()
    assert "refinement" in raw.lower(), "expected REFINEMENT section header"


def test_template_lists_qualifier_vocabulary_in_three_languages() -> None:
    """CCS-04: refinement rule must enumerate qualifier vocabulary for en/uk/ru."""
    raw = _load()
    for word in ("actually", "but", "instead", "just", "only"):
        assert word in raw, f"expected English qualifier {word!r}"
    for word in ("насправді", "але", "тільки", "краще"):
        assert word in raw, f"expected Ukrainian qualifier {word!r}"
    for word in ("вообще-то", "но", "только", "лучше"):
        assert word in raw, f"expected Russian qualifier {word!r}"


def test_template_has_three_refinement_examples() -> None:
    """CCS-04: at least one refinement example per language (en/uk/ru), each
    emitting a new web_search intent that combines prior query + qualifier."""
    raw = _load()
    # Locate the REFINEMENT section header
    idx = raw.lower().index("refinement")
    block = raw[idx:]
    # Expect at least three "User (...refining" examples in this block, one per language
    assert "User (English, refining" in block, "missing English refinement example"
    assert "User (Ukrainian, refining" in block, "missing Ukrainian refinement example"
    assert "User (Russian, refining" in block, "missing Russian refinement example"
    # Each refinement example should emit a new web_search intent with combined args
    en_idx = block.index("User (English, refining")
    en_block = block[en_idx:en_idx + 400]
    assert '"tool":"web_search"' in en_block, "EN refinement example missing web_search intent"
    assert "vegan" in en_block and "chili" in en_block, (
        "EN refinement combined args must include both terms"
    )
    uk_idx = block.index("User (Ukrainian, refining")
    uk_block = block[uk_idx:uk_idx + 400]
    assert '"tool":"web_search"' in uk_block, "UK refinement example missing web_search intent"
    assert "веганськ" in uk_block, "UK refinement combined args must include the qualifier"
    ru_idx = block.index("User (Russian, refining")
    ru_block = block[ru_idx:ru_idx + 400]
    assert '"tool":"web_search"' in ru_block, "RU refinement example missing web_search intent"
    assert "веган" in ru_block, "RU refinement combined args must include the qualifier"


def test_template_refinement_rule_states_recency_window_in_plain_language() -> None:
    """CCS-04: rule must state the recency window in plain English so the LLM
    can apply it without numeric arithmetic."""
    raw = _load()
    assert "within the last 10 minutes" in raw, (
        "refinement rule must state '10 minutes' recency window in plain language"
    )


def test_template_refinement_rule_has_topic_unrelated_guard() -> None:
    """CCS-04: rule explicitly forbids emitting a generic web_search if the
    qualifier is unrelated to the prior topic."""
    raw = _load()
    lowered = raw.lower()
    assert "unrelated to the prior topic" in lowered, (
        "missing 'unrelated to the prior topic' guard in REFINEMENT rule"
    )


def test_template_refinement_rule_has_outside_window_fallthrough() -> None:
    """CCS-04: when prior web_search is older than recency window, model must
    issue a fresh web_search rather than combine with stale prior query."""
    raw = _load()
    lowered = raw.lower()
    assert "more than 10 minutes ago" in lowered, (
        "REFINEMENT rule must describe the >10-min stale fallthrough"
    )


def test_substitution_leaves_no_placeholders() -> None:
    raw = _load()
    ctx = {
        "persona": "You are Heare, a warm companion.",
        "time": "2026-04-18 01:23:45",
        "timezone": "Europe/Kiev",
        "recent_transcripts": "  - [01:23:00] Hello.",
        "transcript": "How are you?",
        "conversation_summary": "discussed plans",
        "active_topics": "plans, bread",
        "entities": "  - topics: plans, bread",
        "recent_turns": "  - [01:22:00] Something.",
        "recent_actions": "  - [01:20:00] ✓ bash: echo",
        "mcp_servers": "chrome-devtools, filesystem",
        "user_language": "Ukrainian",
    }
    rendered = _safe_substitute(raw, ctx)
    import re

    leftover = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", rendered)
    assert leftover == [], f"leftover placeholders: {leftover}"
    for value in ctx.values():
        assert value.strip().split("\n")[0] in rendered
