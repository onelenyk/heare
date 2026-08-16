"""The artifact writer — the part that used to hide in a closure."""
from __future__ import annotations

from datetime import datetime

import pytest

from src.spine.artifacts import artifact_name, save_artifact, strip_code_fence


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("```markdown\n# Підсумок\nтекст\n```", "# Підсумок\nтекст"),
        ("```\n# Підсумок\n```", "# Підсумок"),
        ("# Підсумок\nбез огорожі", "# Підсумок\nбез огорожі"),
        ("  ```md\nтіло\n```  ", "тіло"),
        # Cut off mid-document: the body is worth more than the syntax.
        ("```markdown\n# Початок\nтекст без кінця", "# Початок\nтекст без кінця"),
    ],
)
def test_a_fence_around_the_whole_document_is_removed(raw, expected) -> None:
    assert strip_code_fence(raw) == expected


def test_a_fence_inside_the_document_is_content() -> None:
    """A protocol may quote a command; that fence is what was said."""
    doc = "# Мітинг\n\nВирішили запускати:\n\n```bash\ndf -h /\n```\n\nВсе."
    assert strip_code_fence(doc) == doc


def test_empty_input() -> None:
    assert strip_code_fence("") == ""
    assert strip_code_fence(None) == ""  # type: ignore[arg-type]


def test_name_is_readable_and_safe() -> None:
    when = datetime(2026, 8, 16, 14, 25)
    assert artifact_name("мітинг", when) == "2026-08-16-1425-мітинг.md"
    assert artifact_name("роль/../etc", when) == "2026-08-16-1425-роль-etc.md"
    assert artifact_name("", when) == "2026-08-16-1425-роль.md"


def test_save_writes_the_document_not_a_quote_of_it(tmp_path) -> None:
    path = save_artifact(
        tmp_path / "artifacts",
        "мітинг",
        "```markdown\n# Протокол\n- рішення\n```",
        datetime(2026, 8, 16, 14, 25),
    )
    written = (tmp_path / "artifacts" / "2026-08-16-1425-мітинг.md").read_text("utf-8")
    assert written == "# Протокол\n- рішення\n"
    assert path.endswith("2026-08-16-1425-мітинг.md")
