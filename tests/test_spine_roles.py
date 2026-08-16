"""Test suite for src.spine.roles.

Table-driven tests over tmp_path role files, covering RoleLoader parsing
(frontmatter shapes, defaults, skip-on-error, override-on-clash) and the
two trigger-matching functions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.spine.roles import Role, RoleLoader, is_end_trigger, match_trigger


# ============================================================================
# RoleLoader — parsing
# ============================================================================


def test_full_frontmatter_parses_into_role(tmp_path: Path):
    """All keys present, with both inline-list and dashed multi-line list
    forms, parse into the expected Role fields."""
    (tmp_path / "interview.md").write_text(
        """---
name: Interview
channel: log
deny_tools: [shell.*, fs.write*]
artifact: A transcript summary with key quotes
triggers:
  - режим інтерв'ю
  - почни інтерв'ю
end_triggers: [стоп інтерв'ю, все, дякую]
---

# Interview role

Ask one question at a time. Do not interrupt.
""",
        encoding="utf-8",
    )

    roles = RoleLoader([tmp_path]).load()

    assert set(roles) == {"interview"}
    role = roles["interview"]
    assert role.name == "interview"
    assert role.channel == "log"
    assert role.deny_tools == ("shell.*", "fs.write*")
    assert role.artifact == "A transcript summary with key quotes"
    assert role.triggers == ("режим інтерв'ю", "почни інтерв'ю")
    assert role.end_triggers == ("стоп інтерв'ю", "все", "дякую")
    assert role.prompt == "# Interview role\n\nAsk one question at a time. Do not interrupt."


def test_missing_name_is_skipped_with_warning_other_files_still_load(
    tmp_path: Path, caplog
):
    """A role file without a 'name' key is skipped (with a warning), but
    a sibling valid file in the same directory still loads."""
    (tmp_path / "noname.md").write_text(
        """---
channel: voice
---
Body without a name.
""",
        encoding="utf-8",
    )
    (tmp_path / "valid.md").write_text(
        """---
name: valid
---
Valid body.
""",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        roles = RoleLoader([tmp_path]).load()

    assert set(roles) == {"valid"}
    assert any("name" in rec.message.lower() for rec in caplog.records)


def test_later_path_overrides_earlier_same_name_role(tmp_path: Path):
    """Two search paths defining a role with the same name: the later
    path in the list wins."""
    early_dir = tmp_path / "early"
    late_dir = tmp_path / "late"
    early_dir.mkdir()
    late_dir.mkdir()

    (early_dir / "r.md").write_text(
        """---
name: shared
channel: voice
artifact: early artifact
---
Early prompt.
""",
        encoding="utf-8",
    )
    (late_dir / "r.md").write_text(
        """---
name: shared
channel: log
artifact: late artifact
---
Late prompt.
""",
        encoding="utf-8",
    )

    roles = RoleLoader([early_dir, late_dir]).load()

    assert set(roles) == {"shared"}
    assert roles["shared"].channel == "log"
    assert roles["shared"].artifact == "late artifact"
    assert roles["shared"].prompt == "Late prompt."


def test_defaults_for_absent_keys(tmp_path: Path):
    """A role file with only 'name' set gets voice channel, empty
    deny_tools/artifact/triggers, and the default end_triggers."""
    (tmp_path / "bare.md").write_text(
        """---
name: bare
---
Minimal body.
""",
        encoding="utf-8",
    )

    roles = RoleLoader([tmp_path]).load()

    role = roles["bare"]
    assert role.channel == "voice"
    assert role.deny_tools == ()
    assert role.artifact == ""
    assert role.triggers == ()
    assert role.end_triggers == ("закінчили", "кінець ролі", "завершуй роль")


def test_body_markdown_lands_in_prompt_verbatim(tmp_path: Path):
    """The markdown body after the frontmatter closing '---' is stored in
    .prompt, stripped of leading/trailing whitespace but otherwise
    untouched — headings, lists, blank lines all preserved."""
    body = (
        "# Role\n\n"
        "- one\n"
        "- two\n\n"
        "Some *emphasis* and a [link](http://example.com).\n"
    )
    (tmp_path / "md.md").write_text(
        "---\nname: markdowny\n---\n\n" + body + "\n",
        encoding="utf-8",
    )

    roles = RoleLoader([tmp_path]).load()

    assert roles["markdowny"].prompt == body.strip()


# ============================================================================
# match_trigger
# ============================================================================


def _role(name: str, triggers: tuple[str, ...]) -> Role:
    return Role(name=name, triggers=triggers)


MATCH_TRIGGER_CASES = [
    pytest.param(
        {"interview": _role("interview", ("режим інтерв'ю",))},
        "Дока, режим інтерв'ю будь ласка",
        "interview",
        id="match-inside-longer-sentence",
    ),
    pytest.param(
        {"interview": _role("interview", ("режим інтерв'ю",))},
        "ДОКА, РЕЖИМ Інтерв'ю будь ласка",
        "interview",
        id="case-insensitive-ukrainian",
    ),
    pytest.param(
        {
            "short": _role("short", ("інтерв'ю",)),
            "long": _role("long", ("режим інтерв'ю",)),
        },
        "почни режим інтерв'ю зараз",
        "long",
        id="longest-trigger-wins-across-roles",
    ),
    pytest.param(
        {"interview": _role("interview", ("режим інтерв'ю",))},
        "просто поговоримо про погоду",
        None,
        id="no-match-returns-none",
    ),
    pytest.param(
        {},
        "anything at all",
        None,
        id="no-roles-returns-none",
    ),
]


@pytest.mark.parametrize("roles, text, expected_name", MATCH_TRIGGER_CASES)
def test_match_trigger(roles, text, expected_name):
    result = match_trigger(text, roles)
    if expected_name is None:
        assert result is None
    else:
        assert result is not None
        assert result.name == expected_name


# ============================================================================
# is_end_trigger
# ============================================================================


IS_END_TRIGGER_CASES = [
    pytest.param("ну все, закінчили", True, id="phrase-inside-longer-sentence"),
    pytest.param("ЗАКІНЧИЛИ", True, id="case-insensitive-exact"),
    pytest.param("кінець ролі, дякую", True, id="alternate-default-trigger"),
    pytest.param("продовжуємо далі", False, id="unrelated-text-no-match"),
    pytest.param("", False, id="empty-text-no-match"),
]


@pytest.mark.parametrize("text, expected", IS_END_TRIGGER_CASES)
def test_is_end_trigger(text, expected):
    role = Role(name="interview")  # default end_triggers
    assert is_end_trigger(text, role) is expected


def test_readme_is_not_mistaken_for_a_role(tmp_path) -> None:
    """A roles folder explains itself to humans; that page warned on
    every daemon boot as a malformed role."""
    (tmp_path / "README.md").write_text("# Ролі\nЯк додати свою роль…\n", "utf-8")
    (tmp_path / "meeting.md").write_text(
        "---\nname: мітинг\nchannel: log\ntriggers: [почни мітинг]\n---\nсекретар\n",
        "utf-8",
    )
    roles = RoleLoader([tmp_path]).load()
    assert sorted(roles) == ["мітинг"]
